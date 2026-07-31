"""Instance messaging service for sending and processing messages."""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NamedTuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from sqlmodel import Session

from ..cancellation import CancellationToken
from ..compaction import ContextCompactor, CompactionContext, get_model_context_limit
from ..language_detection import _normalize_content
from ..loader import estimate_messages_tokens
from ..persistence import get_instance_messages
from ..repositories.event.models import Event, EventKind
from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from ..repositories.task.models import Task, TaskType, TaskStatus
from ..utils import parse_think_tags, serialize_message
from ..write_pause_guard import WriteGuardSession
from .cancellation import CancellationService
from .main_loop_bridge import MainLoopBridge
from .messaging_types import AsyncMessageResult
from .project_normalizer import normalize_project_id
from .skill_meta_parser import extract_load_skill, parse_meta_tag
from .skill_metrics_service import (
    AUTO_LOAD_BLOCK_ACTIVE_KEY,
    INJECTED_SKILLS_METADATA_KEY,
    REPLACED_SKILLS_METADATA_KEY,
)

if TYPE_CHECKING:
    from ..config import Config
    from ..graph import CompiledStateGraph
    from ..repositories.instance.repository import SQLModelInstanceRepository
    from ..repositories.message_queue.repository import MessageQueueRepository
    from ..repositories.project.repository import SQLModelProjectRepository
    from .child_reports import ChildReportsService
    from .event_publisher import EventPublisherService
    from .error_reporting import ErrorReportingService


logger = logging.getLogger(__name__)


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


def _stringify_tool_message_content(m) -> tuple[str, str]:
    """Extract `(tool_call_id, content_str)` from a ToolMessage.

    Centralized so the bake-in path (into the next AIMessage's tool_calls)
    and the real-time tool_result SSE path cannot drift.

    Args:
        m: A LangChain ToolMessage.

    Returns:
        Tuple of `(tool_call_id, content_str)`. Either may be empty.
    """
    tc_id = getattr(m, "tool_call_id", "") or ""
    raw_content = getattr(m, "content", "") or ""
    content_str = raw_content if isinstance(raw_content, str) else str(raw_content)
    return tc_id, content_str


def _dedup_merge_skill_ids(
    instance_repository: Any,
    instance_id: str,
    new_ids: list[str],
) -> None:
    """Read-merge-write ``last_injected_skill_ids`` (DEDUP-MERGE) for one instance.

    Centralizes the read-modify-write of :data:`INJECTED_SKILLS_METADATA_KEY`
    so the BM25-skill persist and the auto-load-skill persist share one
    implementation. Order-preserving union via ``dict.fromkeys``: existing
    (explicit) IDs first, then ``new_ids`` appended, duplicates dropped.

    No filtering is applied here — REPLACE/REPLACED filtering is the
    caller's responsibility (auto-load: :func:`_fetch_auto_load_skills`;
    BM25: ``SkillInjectionService``). Keeping the merge filter-free means
    a future exclusion rule has exactly one site to update per producer.

    Args:
        instance_repository: Repository exposing ``get`` (returning an
            instance row whose ``instance_metadata`` is a dict) and
            ``set_metadata``.
        instance_id: Target instance.
        new_ids: Skill IDs to merge into the existing set.
    """
    inst = instance_repository.get(instance_id)
    existing: list[str] = []
    if inst is not None and inst.instance_metadata:
        raw = inst.instance_metadata.get(INJECTED_SKILLS_METADATA_KEY) or []
        if isinstance(raw, list):
            existing = [str(x) for x in raw if x]
    merged = list(dict.fromkeys(existing + [str(x) for x in new_ids if x]))
    instance_repository.set_metadata(
        instance_id,
        INJECTED_SKILLS_METADATA_KEY,
        merged,
    )


def _build_graph_input(
    content: str | list,
    message_id: str,
    persistent_context_msgs: list[HumanMessage] | None = None,
) -> dict[str, list[HumanMessage]]:
    """Build the LangGraph ``graph_input`` dict, prepending the persistent context block.

    Phase 3 helper used by all three ``graph_input = ...`` construction
    sites in :meth:`InstanceMessagingService._process_message_with_tracking`.
    Centralizing the construction ensures the prepend order is identical
    across the retry-with-checkpoint, retry-without-checkpoint, and
    first-attempt branches — a divergence there would silently double-
    inject (or skip-inject) on retries.

    Hybrid Context Injection (2026-07-29): the
    ``persistent_context_msgs`` arg carries the project + shared-
    context **+ skills** HumanMessages built by the messaging path.
    When provided (non-empty), they are prepended to ``graph_input``
    so LangGraph's ``add_messages`` reducer checkpoints them BEFORE
    the user message — the persistent block then lives in
    ``state['messages']`` for every subsequent turn without any
    per-turn rebuild. The user message keeps its ``message_id`` for
    reducer dedup, and the persistent messages keep their own uuids
    (generated by ``_make_context_message``) so they survive
    checkpoint serialisation as stable, identifiable rows.

    2026-07-29 refactor: skills were moved from the ephemeral
    (``agent_node``-side rebuild every turn) into this persistent
    block. A skill injected on turn 1 is now a checkpointed
    ``HumanMessage`` that survives every subsequent turn via
    ``state['messages']``. A new skill triggered on turn 2 is
    APPENDED to the persistent half (LangGraph ``add_messages``
    reducer semantics) and prepended to ``graph_input`` for that
    turn — no double-injection because ``agent_node`` no longer
    re-injects skills into ``full_messages``.

    Args:
        content: The user message content (string or multimodal
            content-block list from ``_build_message_content``).
        message_id: The queue message ID; becomes the user
            ``HumanMessage.id`` for ``add_messages`` dedup.
        persistent_context_msgs: Optional list of
            :class:`HumanMessage` carrying the persistent block
            (project + shared-context + skills). Prepended BEFORE
            the user message so the LangGraph ``add_messages``
            reducer checkpoints them with the user message.
            ``None`` (default) and ``[]`` both mean "no persistent
            block this turn" — every turn after the first, or any
            turn when persistent context is empty.

    Returns:
        ``{"messages": [...]}`` dict ready for
        ``graph.astream(graph_input, ...)``. With a non-empty
        ``persistent_context_msgs``, the list is
        ``[persistent_1, ..., persistent_n, user_message]`` — the
        persistent block sits BEFORE the user message so it appears
        at the very start of ``state['messages']`` after the first
        ``add_messages`` reducer pass. With an empty
        ``persistent_context_msgs``, the list contains ONLY the
        ``user_message``.
    """
    user_message = HumanMessage(content=content, id=message_id)
    # Hybrid split — prepend the persistent context block BEFORE the
    # user message so LangGraph's ``add_messages`` reducer checkpoints
    # it with the user message. Empty / None ``persistent_context_msgs``
    # produces the steady-state second-turn layout ``[user_message]``.
    # Per the 2026-07-29 refactor this block also carries skills.
    persistent = list(persistent_context_msgs or [])
    return {"messages": persistent + [user_message]}


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

    # Key fields that matter for content comparison
    content_parts = {
        "content": msg.get("content"),
        "tool_calls": msg.get("tool_calls"),
        "role": msg.get("role"),
    }
    # Normalize: sort keys and remove None values for consistent hashing
    content_str = json.dumps(content_parts, sort_keys=True, default=str)
    return hashlib.md5(content_str.encode()).hexdigest()[:16]


class _PreparedEnqueueContext(NamedTuple):
    """Result of `_prepare_enqueued_message` shared prelude.

    Carries the values callers need to perform their path-specific dispatch
    (after D13: unified — WorkerPool Task row + notify, no JobQueue branch).
    """
    message_id: str
    msg_type: str
    status_changed_to_running: bool
    is_idle_to_running: bool
    instance_agent_id: str | None
    previous_status: str | None
    # D13: The Task row is always created in the same transaction as the
    # MessageQueue row. ``task_id`` is its primary key (int | None) — None
    # only if the task insert failed for an unrecoverable reason (callers
    # treat None as "no resolvable work_id available"). The HTTP route
    # discards ``job_id``; the ``job_continue`` tool uses it as
    # ``new_job_id`` (the resolution path goes through
    # ``work_resolver.resolve_work`` against ``task`` and
    # ``job_queue_items`` — see ``enqueue_message``).
    task_id: int | None
    # Virtual Job Management Surface (Phase 1, Batch 3,
    # 2026-06-27). The stable cross-system ``work_id`` (UUID4 string)
    # minted at Task row creation. This is the truthful handle for the
    # virtual job resolver — callers pass it back to ``GET /work/{id}``
    # and ``work_resolver.resolve_work`` looks it up uniformly across
    # ``task`` and ``job_queue_items``. Supersedes ``task_id`` as the
    # ``AsyncMessageResult.job_id`` payload (see
    # ``enqueue_message``); ``task_id`` is retained for callers that
    # still want the int PK (currently nobody does, but it stays in the
    # NamedTuple for the existing test surface). ``None`` only when
    # the Task insert itself failed (mirrors ``task_id``).
    work_id: str | None
    # Defer Queue marker (Phase 3 Part B1, 2026-06-27,
    # feature/virtual-job-management-surface). Mirrors
    # ``Task.is_deferred`` at row-creation time. The orchestrator
    # passes ``is_deferred`` into ``enqueue_message`` /
    # ``_prepare_enqueued_message``; the value is stamped onto the new
    # Task row and surfaced here so callers (and the eventual defer
    # queue gate) can read it without re-querying the DB. Always False
    # for the default (non-defer) path — every existing caller that
    # does not pass ``is_deferred`` is unaffected.
    is_deferred: bool


class ActivityCallbackHandler(BaseCallbackHandler):
    """Callback to update message activity during LLM/graph execution.
    
    This ensures long-running tasks are not incorrectly marked as "stuck"
    by the worker pool health checks, as long as there's recent activity.
    """
    
    def __init__(self, queue_repository, message_id: str, update_interval_seconds: float = 5.0):
        """Initialize with message queue repository.
        
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
    
    def on_llm_end(self, response: Any, **kwargs) -> None:
        self._maybe_update()
    
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self._maybe_update()
    
    def on_tool_end(self, output: Any, **kwargs) -> None:
        self._maybe_update()
    
    def on_chain_start(self, serialized, inputs: Any, **kwargs) -> None:
        self._maybe_update()
    
    def on_chain_end(self, outputs: Any, **kwargs) -> None:
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
    
    def on_llm_start(self, serialized, prompts: Any, **kwargs) -> None:
        """Check cancellation before LLM call."""
        self._check_cancellation()
    
    def on_llm_new_token(self, token: str, **kwargs) -> None:
        """Check cancellation periodically during streaming."""
        self._token_count += 1
        if self._token_count % self._check_interval == 0:
            self._check_cancellation()
    
    def on_tool_start(self, serialized, input_str: Any, **kwargs) -> None:
        """Check cancellation before tool execution."""
        self._check_cancellation()
    
    def on_chain_start(self, serialized, inputs: Any, **kwargs) -> None:
        """Check cancellation before chain step."""
        self._check_cancellation()


class InstanceMessagingService:
    """Service for sending and processing messages to/from instances.
    
    Handles:
    - Direct message sending (send_message)
    - Message queuing (enqueue_message)
    - Message processing with tracking (_process_message_with_tracking)
    - Message history retrieval (get_messages)
    - Queue statistics (get_queue_stats)
    """

    def __init__(
        self,
        manager: "InstanceManager",
        cancellation_service: "CancellationService",
        child_reports_service: "ChildReportsService | None" = None,
        events_service: "EventPublisherService | None" = None,
    ):
        """Initialize the messaging service.
        
        Args:
            manager: The InstanceManager facade.
            cancellation_service: Service for cancellation handling.
            child_reports_service: Service for child completion reports.
            events_service: Service for lifecycle event publishing.
        """
        self._manager = manager
        self._cancellation_service = cancellation_service
        self._child_reports_service = child_reports_service
        self._events_service = events_service

    @property
    def _config(self) -> "Config":
        """Access config through manager for test mockability."""
        return self._manager.config

    @property
    def _queue_repository(self) -> "MessageQueueRepository":
        """Access queue repository through manager for test mockability."""
        return self._manager._queue_repository

    @property
    def _project_repository(self) -> "SQLModelProjectRepository":
        """Access project repository through manager for test mockability."""
        return self._manager._project_repository

    @property
    def _prompt_cache(self) -> Any:
        """Access prompt cache through manager for test mockability."""
        return self._manager.prompt_cache

    @property
    def _llm_semaphore(self) -> asyncio.Semaphore:
        """Access LLM semaphore through manager for test mockability."""
        return self._manager._llm_semaphore

    @property
    def _compactor(self) -> "ContextCompactor | None":
        """Access compactor through manager for test mockability."""
        return self._manager._compactor

    @property
    def _checkpointer(self) -> "Any | None":
        """Access the underlying LangGraph checkpointer (saver) through manager.

        Phase 2 migration: the manager now stores a ``CheckpointerAdapter``;
        services that need the raw saver (``aget`` / ``alist``) reach it via
        ``raw_saver``. ``maintenance.py`` uses the adapter interface directly.

        Returns ``None`` if the checkpointer has not been initialized yet.
        """
        adapter = self._manager._checkpointer
        return adapter.raw_saver if adapter is not None else None

    def _resolve_agent_meta_from_row(self, instance_row: Any) -> Any | None:
        """Resolve the agent metadata for an instance row (best-effort).

        The canonical versioned-resolution path (``get_version`` →
        ``get_resolved`` fallback) shared by every messaging site that
        needs the agent's metadata — both the per-instance recursion
        limit (:meth:`_effective_recursion_limit`) and the streaming
        path's ``_messaging_agent_meta`` (context injection) resolve
        through here so the fallback logic lives in one place. Returns
        ``None`` on any failure (missing row, unknown agent_id,
        registry error) so callers fall back to safe defaults.

        ``get_registry`` is imported locally so tests that patch
        ``daemon.registry.get_registry`` (or the module-level binding)
        remain effective.

        Args:
            instance_row: An instance ORM row exposing ``agent_id`` and
                (optionally) ``agent_tag``. ``None`` → ``None``.

        Returns:
            The :class:`~daemon.registry.AgentMetadata`, or ``None``.
        """
        if instance_row is None:
            return None
        agent_id = getattr(instance_row, "agent_id", None)
        if not agent_id:
            return None
        try:
            from ..registry import get_registry
            registry = get_registry()
            return (
                registry.get_version(
                    agent_id, getattr(instance_row, "agent_tag", None)
                )
                or registry.get_resolved(agent_id)
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                f"[Messaging] Failed to resolve agent_meta for "
                f"agent_id={agent_id!r}: {exc}"
            )
            return None

    def _resolve_recursion_limit_for_meta(self, agent_meta: Any | None) -> int:
        """Compute the effective LangGraph ``recursion_limit`` from agent metadata.

        Thin wrapper over :func:`daemon.registry.resolve_recursion_limit`
        so the ``resolve_recursion_limit`` import lives in exactly one
        messaging site (kept local for test-mockability parity with
        :meth:`_resolve_agent_meta_from_row`). Applies the agent's
        ``recursion_limit`` / ``recursion_limit_multiplier`` (declared in
        ``meta.json``) on top of the global
        ``limits.graph_recursion_limit`` so long-running working agents
        (e.g. worker, coder) get a larger step quota.

        Args:
            agent_meta: Pre-resolved agent metadata (may be ``None``).

        Returns:
            The effective recursion limit as a positive ``int``.
        """
        from ..registry import resolve_recursion_limit
        return resolve_recursion_limit(
            self._config.limits.graph_recursion_limit, agent_meta
        )

    def _effective_recursion_limit(self, instance_row: Any) -> int:
        """Compute the per-instance LangGraph ``recursion_limit``.

        Convenience composition: resolve the agent metadata from the
        instance row, then apply the per-agent override / multiplier.
        Use :meth:`_resolve_recursion_limit_for_meta` directly when the
        metadata is already resolved (e.g. the streaming path's
        ``_messaging_agent_meta``) to avoid re-resolution.

        Args:
            instance_row: The instance ORM row used to resolve the
                agent metadata (may be ``None``).

        Returns:
            The effective recursion limit as a positive ``int``.
        """
        return self._resolve_recursion_limit_for_meta(
            self._resolve_agent_meta_from_row(instance_row)
        )

    async def _get_system_prompt_tokens(self, instance_id: str) -> int:
        """Get the cached system prompt token count for an instance's agent.

        Async because the underlying ``_instance_repository.get`` is a sync
        SQLAlchemy call that, under SQLite WAL write contention, can block
        the event loop. We offload it to a worker thread via
        ``asyncio.to_thread`` (see deadlock analysis in experience docs).
        """
        try:
            meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if not meta:
                return 0
            # Get cached token count from prompt cache using agent_id + mcp_tool_names
            mcp_tool_names = meta.instance_metadata.get("mcp_tool_names")
            cached = self._prompt_cache.get(meta.agent_id, mcp_tool_names)
            if cached is not None:
                _, token_count = cached
                return token_count
            return 0
        except Exception:
            return 0

    async def _compute_context_usage(
        self,
        instance_id: str,
        messages: list,
    ) -> tuple[int, int, str] | None:
        """Compute the current context usage snapshot for an instance.

        Returns (tokens, context_window, model_name) or None if the model
        cannot be resolved (e.g. instance missing). The token count is
        history tokens + cached system prompt tokens so it matches what
        ``_maybe_compact_context`` measures internally.

        Async because it calls the async ``_get_system_prompt_tokens`` which
        offloads the sync SQLAlchemy ``_instance_repository.get`` to a worker
        thread (see deadlock analysis in experience docs).

        Args:
            instance_id: The instance to compute usage for.
            messages: The current message list (LangChain BaseMessage objects
                or dicts). Empty/None is fine — we still return a snapshot.
        """
        try:
            model_name = self._config.llm.model or ""
            context_window = get_model_context_limit(model_name, self._config.compaction)
            history_tokens = estimate_messages_tokens(messages or [])
            system_prompt_tokens = await self._get_system_prompt_tokens(instance_id)
            return history_tokens + system_prompt_tokens, context_window, model_name
        except Exception as e:
            logger.debug(f"Failed to compute context usage for {instance_id[:8]}...: {e}")
            return None

    async def _emit_context_usage(
        self,
        instance_id: str,
        messages: list,
        force: bool = False,
    ) -> None:
        """Compute and broadcast a context_usage event, suppressing duplicates.

        Compares against the last snapshot broadcast for this instance; if
        the token count is unchanged, the call is a no-op so the SSE
        stream isn't polluted with redundant updates. The check is per-
        process; an instance with N active SSE connections pays the cost
        once per call regardless of N.

        Pass ``force=True`` to skip the dedup check — used by the SSE
        connect handler so the first event for a freshly connected
        client always gets through, even if the instance was recently
        snapshotted for another client.

        Args:
            instance_id: The instance to snapshot.
            messages: The current message list.
            force: If True, skip the dedup check and always broadcast.
        """
        snapshot = await self._compute_context_usage(instance_id, messages)
        if snapshot is None:
            return
        tokens, context_window, model_name = snapshot

        if not force:
            last = self._manager._last_context_usage.get(instance_id)
            # Suppress if the token count hasn't moved (typical while a long
            # assistant response is streaming one token at a time — only the
            # final value changes). 1-token jitter is ignored.
            if last is not None and abs(last - tokens) < 1 and tokens > 0:
                return
        self._manager._last_context_usage[instance_id] = tokens

        try:
            await self._manager._live_hub.stream_context_usage(
                instance_id=instance_id,
                tokens=tokens,
                context_window=context_window,
                model_name=model_name,
            )
        except Exception as e:
            logger.debug(f"Failed to broadcast context usage for {instance_id[:8]}...: {e}")

    async def emit_context_usage_for_instance(self, instance_id: str) -> None:
        """Public wrapper: load current state messages and emit context usage.

        Used by the SSE connect handler to populate the FE indicator
        immediately on connect, before any user interaction. Any failure
        is logged at debug level and swallowed so a transient checkpointer
        hiccup never breaks the SSE connection.

        Reads raw LangGraph state messages directly from the checkpoint,
        matching the SSE path (``all_state_messages`` in the astream loop).
        Going through ``get_messages`` would route messages through
        ``serialize_message`` and ``get_instance_messages``, which (a) skip
        ``ToolMessage`` entries entirely, (b) strip thinking content, and
        (c) rewrite tool-call arg keys from ``args`` to ``arguments``,
        producing an inflated-by-omission or otherwise incorrect token
        count on initial page load. Passing raw ``BaseMessage`` objects
        straight to ``estimate_messages_tokens`` keeps the snapshot in sync
        with what the SSE update path computes.
        """
        # Verify instance exists first so a missing instance returns
        # silently without poking the checkpointer.
        try:
            await self._manager.get_instance(instance_id)
        except Exception as e:
            logger.debug(
                f"emit_context_usage_for_instance: instance lookup failed for "
                f"{instance_id[:8]}...: {e}"
            )
            return

        # The service-level ``_checkpointer`` property already unwraps a
        # ``CheckpointerAdapter`` to its raw saver (see the property below)
        # so SQLite and PostgreSQL backends are both supported without any
        # extra plumbing here.
        saver = self._checkpointer
        if saver is None:
            return

        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await saver.aget(config)
            if state is None:
                await self._emit_context_usage(instance_id, [], force=True)
                return
            channel_values = state.get("channel_values", {}) or {}
            messages = channel_values.get("messages", []) or []
        except Exception as e:
            logger.debug(
                f"emit_context_usage_for_instance: raw checkpoint read failed for "
                f"{instance_id[:8]}...: {e}"
            )
            return

        await self._emit_context_usage(instance_id, messages, force=True)

    async def _maybe_compact_context(
        self,
        instance_id: str,
        graph: "CompiledStateGraph",
        config: dict[str, Any],
    ) -> None:
        """Conditionally compact instance context if threshold is exceeded."""
        if self._compactor is None:
            return
        
        try:
            # Get current state
            state = await graph.aget_state(config)
            if not state:
                return

            # ── Terminal-checkpoint guard ───────────────────────────────
            # Skip compaction entirely when the checkpoint is terminal
            # (state.next is empty/None). On a finished graph, calling
            # ``aupdate_state(as_node="agent")`` below would clear the
            # checkpoint's ``next=()``, causing the subsequent
            # ``astream(graph_input)`` to return instantly without running
            # the agent. On reuse of a completed instance this collapses
            # the COMPLETED→RUNNING→COMPLETED cycle to <100ms so the
            # frontend never observes RUNNING.
            #
            # Compaction is an optimization — skipping it here is safe:
            # the new message is passed as ``graph_input`` to ``astream``
            # and the agent runs against the full (uncompacted) history
            # for this turn. Active (non-terminal) turns compact normally.
            if not state.next:
                logger.debug(
                    f"[Compaction] Skipping on terminal checkpoint for {instance_id[:8]}..."
                )
                return

            messages = state.values.get('messages', [])
            system_prompt_tokens = await self._get_system_prompt_tokens(instance_id)
            last_compacted_at = state.values.get('compacted_at')
            
            # Build compaction context
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=system_prompt_tokens,
                model_name=self._config.llm.model,
                config=self._config.compaction,
                llm_config={
                    "base_url": self._config.llm.base_url,
                    "api_key": self._config.llm.api_key,
                    "model": self._config.llm.model,
                    "model_vision": self._config.llm.model_vision,
                    "temperature": self._config.llm.temperature,
                    "request_timeout": self._config.llm.request_timeout,
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
        """Check if a checkpoint exists for this instance."""
        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await self._checkpointer.aget(config)
            result = state is not None
            channel_values = state.get("channel_values", {}) if state else {}
            msg_count = len(channel_values.get("messages", []))
            logger.info(f"[RESUME] instance={instance_id[:8]} has_checkpoint={result}, msg_count={msg_count}")
            return result
        except Exception as e:
            logger.info(f"[RESUME] instance={instance_id[:8]} has_checkpoint=False, exception={type(e).__name__}")
            return False

    async def _get_message_count(self, instance_id: str) -> int:
        """Get the number of messages in the instance's checkpoint/state."""
        try:
            config = {"configurable": {"thread_id": instance_id}}
            state = await self._checkpointer.aget(config)
            if state:
                channel_values = state.get("channel_values", {})
                messages = channel_values.get("messages", [])
                return len(messages) if messages else 0
            return 0
        except Exception:
            return 0

    def _maybe_trigger_title_generation(self, instance_id: str, message: str, should_trigger: bool) -> None:
        """Fire-and-forget title generation and initiative-message capture if conditions are met."""
        if should_trigger:
            MainLoopBridge.run_async_no_wait(
                self._manager._generate_and_broadcast_title(instance_id, message)
            )
            MainLoopBridge.run_async_no_wait(
                self._maybe_store_initiative_message(instance_id, message)
            )
            logger.debug(f"Title generation triggered for first message to instance {instance_id[:8]}...")

    async def _maybe_store_initiative_message(self, instance_id: str, message: str) -> None:
        """Persist the first real user message as ``initiative_message``.

        Captured on the IDLE -> RUNNING transition (the same hook used for
        title generation). First message wins: subsequent transitions are
        no-ops because ``initiative_message`` is already present. Stores a
        truncated (1000-char) copy via the atomic
        :meth:`SQLModelInstanceRepository.set_metadata` so concurrent writes
        against different metadata keys compose correctly.
        """
        # Read the instance off-loop to avoid sync DB writes on the event loop.
        instance = await asyncio.to_thread(
            self._manager._instance_repository.get, instance_id
        )
        if instance is None:
            return
        # Idempotent guard: first message wins.
        if instance.instance_metadata and "initiative_message" in instance.instance_metadata:
            logger.debug(
                f"Initiative message already set for instance {instance_id[:8]}..., skipping"
            )
            return
        if not message or not message.strip():
            return
        truncated_message = message[:1000]
        await asyncio.to_thread(
            self._manager._instance_repository.set_metadata,
            instance_id,
            "initiative_message",
            truncated_message,
        )
        logger.debug(f"Initiative message stored for instance {instance_id[:8]}...")

    async def send_message(self, instance_id: str, message: str) -> "MessageResult":
        """Send a message to an instance and get the response.

        Args:
            instance_id: The ID of the instance to send the message to.
            message: The message content to send.

        Returns:
            MessageResult with content, thinking, and tool_calls.

        Raises:
            KeyError: If instance_id is not found.
        """
        from ..manager import MessageResult
        
        # Get instance graph (will lazy-load from DB if needed)
        # Note: get_instance() now handles MCP preload internally
        graph = await self._manager.get_instance(instance_id)
        
        # Check if this is the first message (instance was IDLE)
        # This determines if we should trigger title generation
        # Wrap the sync DB read in ``asyncio.to_thread`` so SQLite WAL write
        # contention cannot block the event loop (the deadlock chain documented
        # in the experience docs is rooted in sync DB calls on the loop thread).
        instance_meta = await asyncio.to_thread(
            self._manager._instance_repository.get, instance_id
        )
        is_first_message = (
            instance_meta is not None and
            instance_meta.status == InstanceStatus.IDLE.value
        )

        # Register current task for cancellation tracking
        current_task = asyncio.current_task()
        task_registered = False
        if current_task:
            self._manager._graph_tasks[instance_id] = current_task
            task_registered = True
            logger.debug(f"Registered graph task for instance {instance_id[:8]}...")

        # Invoke with message.
        # Use the per-agent recursion-limit override / multiplier so
        # long-running working agents (e.g. worker, coder) get a larger
        # LangGraph step quota than the global default.
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self._effective_recursion_limit(instance_meta),
        }
        
        try:
            # Compact context before processing (non-blocking)
            await self._maybe_compact_context(instance_id, graph, config)

            result = await graph.ainvoke({"messages": [message]}, config)
        except asyncio.CancelledError:
            logger.info(f"Graph execution cancelled for instance {instance_id}")
            # Return a graceful empty result so callers (and the title-generation
            # finally block above) can observe a consistent contract. The
            # title-generation trigger in the finally block still fires, ensuring
            # the first-message title is generated even on cancellation.
            return MessageResult(content="")
        finally:
            # Trigger title generation even on cancellation (fire-and-forget)
            self._maybe_trigger_title_generation(instance_id, message, is_first_message)

            # C2 fix — deferred question pause (Solution A), second pass.
            #
            # ``question_pause_node`` ran inside this graph task and set a
            # marker rather than calling ``pause_instance_cascade`` directly
            # (to avoid self-cancel of this very task). The graph task is now
            # popped from ``_graph_tasks`` so we are safely OUTSIDE the
            # graph-task context — calling the cascade here will not
            # self-cancel; the DB transaction completes normally.
            #
            # HOISTED out of the ``if existing is current_task`` guard below:
            # if an external ``pause_instance_cascade`` already pre-popped
            # ``_graph_tasks[instance_id]`` (e.g. user-click-stop racing the
            # graph completion), the identity check fails and the marker
            # would otherwise leak — causing a spurious pause on the next
            # message. ``pop_deferred_question_pause`` is idempotent
            # (``set.discard``), so it's safe to call unconditionally.
            #
            # C1 FIX (marker lifetime): the marker is PEEKED with
            # ``has_deferred_question_pause`` BEFORE the cascade and POPPED
            # with ``pop_deferred_question_pause`` in the ``finally`` block
            # AFTER the cascade's ``pause_instance_cascade`` completes. The
            # old "pop-before-cascade" ordering left the marker empty during
            # the cascade's DB-commit window (DB still RUNNING) so source-
            # side Task guards saw ``marker=False, db=RUNNING`` and CREATED
            # a spurious Task. Extending the marker lifetime past the
            # cascade's DB commit closes that race. Safe because:
            #   * the marker is in-memory only (no DB write) — moving the
            #     pop cannot introduce a DB torn state;
            #   * the cascade is wrapped in ``asyncio.shield`` so the DB
            #     write completes regardless of outer cancellation;
            #   * ``pause_instance_cascade`` does NOT touch
            #     ``_deferred_question_pause`` (confirmed by grep — no
            #     reference in ``instance_lifecycle.py``);
            #   * ``pop_deferred_question_pause`` is idempotent.
            #
            # SHIELDED against double-cancel: a second ``task.cancel()``
            # arriving during the ``await`` would raise ``CancelledError``
            # (a ``BaseException`` in 3.8+, NOT caught by ``except Exception``).
            # ``asyncio.shield`` protects the DB write so a transient cancel
            # during the pause cascade does not corrupt instance state.
            #
            # Wrapped in try/except so a transient cascade failure does not
            # crash the message-processing call. The question pack SSE has
            # already fired from the tool, so the user can still answer; the
            # instance will just remain in whatever status the graph
            # completed in. Re-pausing an already-PAUSED instance is a
            # no-op (``pause_instance_cascade`` filters out PAUSED nodes
            # at line 1966), so a residual marker on top of an external
            # pause is harmless.
            if self._manager.has_deferred_question_pause(instance_id):
                try:
                    await asyncio.shield(
                        self._manager.pause_instance_cascade(instance_id)
                    )
                except Exception as pause_err:
                    logger.warning(
                        f"[send_message] deferred question pause "
                        f"failed for {instance_id[:8]}...: "
                        f"{type(pause_err).__name__}: {pause_err}"
                    )
                finally:
                    # Pop AFTER the cascade completes so the marker
                    # covers the full cascade-execution window (DB
                    # commit to PAUSED). Closes C1.
                    self._manager.pop_deferred_question_pause(instance_id)

            # Always unregister the task, but only if we're still the registered task
            # (handles race condition where new execution starts before our finally runs)
            if task_registered and current_task:
                existing = self._manager._graph_tasks.get(instance_id)
                if existing is current_task:
                    self._manager._graph_tasks.pop(instance_id, None)
                    self._manager.release_context_usage_cache(instance_id)
                    logger.debug(f"Unregistered graph task for instance {instance_id[:8]}...")

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

    def _prepare_enqueued_message(
        self,
        instance_id: str,
        message: str,
        source: str,
        priority: int,
        images: list[str] | None,
        metadata: dict[str, Any] | None,
        *,
        path_label: str = "",
        is_deferred: bool = False,
        is_background: bool = False,
        work_id: str | None = None,
    ) -> _PreparedEnqueueContext:
        """Shared prelude for ``enqueue_message``.

        Writes the atomic MessageQueue + Task + Event trio that every
        message enqueue needs:

        - Reject messages during shutdown.
        - Resolve ``msg_type`` from the ``source`` prefix and mint a UUID.
        - Insert the ``MessageQueue`` row **unconditionally** (always
          preserved as a durable audit / record).
        - Insert the ``Task`` row **conditionally** — gated by the
          deferred-pause marker guard. When the marker is set (the
          instance is mid-deferred-pause and the cascade's DB commit is
          still in flight), the ``Task`` row is **skipped** to prevent
          ``WorkerPool.claim_pending_task`` from claiming a spurious
          graph turn during the cascade window. The ``MessageQueue``
          row is intentionally preserved as a durable audit record in
          that case (the marker branch always commits the message but
          no Task — see Phase 2 below).
        - Auto-resume ``IDLE`` / ``WAITING_CHILDREN`` / ``COMPLETED`` instances
          to ``RUNNING`` and bump ``last_activity_at`` / ``version``.
        - Append a ``MESSAGE_RECEIVED`` event for event-sourced features.
        - Commit the session.

        **Phase 2 asymmetry** (C2 torn-state / deferred-pause race
        guard, 2026-07): the ``MessageQueue`` row is always created;
        the ``Task`` row is gated by the deferred-pause marker guard
        (skipped when the marker is set). This is intentionally
        asymmetric with the Phase 1 ``child_reports`` guard, which
        checks marker OR ``DB=PAUSED`` — the Phase 2 guard is
        marker-only because ``MessageQueue`` rows have no resume drain
        (``cleanup`` excludes READY rows, so a DB=PAUSED skip would
        orphan an otherwise deliverable message). When the marker
        branch fires:

          * ``ctx.task_id`` is set to ``None`` (callers detect this and
            skip the downstream JobItem creation in
            ``enqueue_message_job`` — see W7 fix).
          * The message in the narrow race window may be lost
            (a known limitation; the durable follow-up is to
            materialize the marker in the DB as
            ``instances.pause_pending`` so Task creation and SQL
            claiming can coexist).

        Args:
            path_label: Optional identifier appended to the "Reactivating
                completed instance" log message. Empty string omits the
                suffix.
            is_deferred: Phase 3 Part B1 (2026-06-27) defer-queue marker.
                When True, the created Task row is stamped
                ``is_deferred=True`` and the worker pool's idle gate
                will hold the task until every non-defer queue is
                empty. Default False preserves the prior behaviour for
                every caller that does not explicitly opt in.
            is_background: Background-queue lane marker. When True, the
                created Task row is stamped ``is_background=True`` so the
                dispatcher routes the work onto the background queue
                instead of the foreground message lane. Default False
                preserves the prior behaviour for every caller that does
                not explicitly opt in (HTTP route, telegram, scheduler,
                internal reports). Independent of ``is_deferred`` — a
                task may be either, both, or neither.

        Returns:
            ``_PreparedEnqueueContext`` carrying the values callers need to
            proceed with dispatch (SSE emit, title generation, WorkerPool
            notify). ``ctx.task_id`` is ``None`` when the marker branch
            fired; ``ctx.message_id`` is always populated.
        """
        # Reject new messages during shutdown
        if self._cancellation_service.is_shutting_down:
            raise RuntimeError("Manager is shutting down, cannot accept new messages")

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

        status_changed_to_running = False
        is_idle_to_running = False
        instance_agent_id: str | None = None
        previous_status: str | None = None
        # A deferred-pause race guard may intentionally omit the Task while
        # preserving the MessageQueue audit row. ``task_id`` is otherwise the
        # Task primary key (int | None); callers already treat None as "no
        # resolvable task id available".
        task_id: int | None = None
        # Virtual Job Management Surface (Phase 1, Batch 3,
        # 2026-06-27): capture ``Task.work_id`` alongside ``task.id``.
        # The Task model's ``work_id`` column has a ``default_factory``
        # that mints a UUID4 at construction, so the value is available
        # immediately after ``session.add(task)`` — no DB round-trip
        # needed to read it (unlike ``task.id``, which requires the
        # post-commit ``refresh()``).
        #
        # Linkage contract (POC ``enqueue_message_job`` path):
        # ``JobItem.job_id`` MUST equal ``Task.work_id``. The caller
        # (``enqueue_message_job``) mints a single UUID and passes it
        # here as ``work_id``; we forward it to the Task row so the
        # two rows share one handle. Legacy callers (``enqueue_message``)
        # pass ``work_id=None``; we mint a UUID here so the Task row
        # has a non-null ``work_id`` regardless of path. Place the
        # auto-generation ONCE, early, and do NOT re-bind ``work_id``
        # later — a bare ``work_id: str | None = None`` re-declaration
        # elsewhere in the method would shadow the parameter, dropping
        # the caller's value on the floor and breaking the linkage.
        if work_id is None:
            work_id = str(uuid.uuid4())

        with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
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
                message_metadata=metadata or {},
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(db_message)

            # C2 TORN-STATE / DEFERRED-PAUSE RACE GUARD (Phase 2).
            # Pausing inside the active graph task can self-cancel the cascade
            # mid-transaction, so set_deferred_question_pause first records the
            # in-memory marker and lets question_pause_node reach graph END.
            # _pause_cascade_db_sync commits status=PAUSED only afterward, leaving
            # a narrow window where the marker is set while the DB still says RUNNING.
            # Creating PROCESS_MESSAGE in that window lets WorkerPool claim a
            # spurious graph turn before the pause commit, reproducing the C2 race.
            # This guard is intentionally MARKER-ONLY, not marker-or-DB-status.
            # READY MessageQueue rows have no resume drain: cleanup excludes READY,
            # so skipping on DB=PAUSED would orphan an otherwise deliverable message.
            # With marker empty and DB=PAUSED we still create the PENDING Task and
            # rely on claim_pending_task's SQL pause gate to hold it until resume.
            # The verified root-resume path bypasses enqueue_message and passes the
            # answer as a fresh message parameter at manager.py:5155; child resume
            # calls enqueue_message with a fresh UUID. Neither consumes this READY row.
            # Thus an in-window skipped row is retained only as a stale audit record;
            # the narrow-window message may be lost, which is a known limitation.
            # The durable follow-up is to materialize this marker in the DB as
            # instances.pause_pending so Task creation and SQL claiming can coexist.
            # Compare Phase 1 in child_reports.py:_process_child_completion_db_sync:
            # that guard deliberately checks marker OR DB=PAUSED because its
            # ReportInjection fallback is drained on every LLM call. Phase 2 is
            # explicitly asymmetric because MessageQueue has no equivalent drain.
            #
            # **Marker lifetime (C1 fix, 2026-07)**: the marker is set in
            # ``question_pause_node``, **peeked** in the post-graph completion
            # path via ``has_deferred_question_pause`` BEFORE awaiting
            # ``pause_instance_cascade``, and **popped** in the inner
            # ``finally`` block AFTER the cascade's DB commit completes. This
            # guard depends on that ordering: if the marker were popped
            # BEFORE the cascade, the guard would see ``marker=False,
            # db=RUNNING`` during the cascade's DB-commit window and CREATE a
            # spurious PROCESS_MESSAGE Task.
            instance_for_pause_guard = session.get(Instance, instance_id)
            deferred_pause_marker_set = (
                instance_id in self._manager._deferred_question_pause
            )
            task: Task | None = None

            # 2. Insert the Task row in the same transaction as the
            #    MessageQueue row unless the in-window marker guard fires.
            #    The structural D13 fix makes Task the dispatch primitive;
            #    preserving MessageQueue while skipping Task prevents the
            #    about-to-be-paused instance from being claimed.
            #
            #    ``is_deferred`` (Phase 3 Part B1, 2026-06-27) is
            #    stamped at creation time so the defer-queue idle gate
            #    can recognise the row without a follow-up UPDATE.
            #    Default False matches every pre-existing caller; the
            #    orchestrator opts in via ``enqueue_message``.
            if deferred_pause_marker_set:
                logger.warning(
                    f"instance_messaging: SKIPPING PROCESS_MESSAGE Task creation "
                    f"for instance {instance_id[:8]}... — reason=marker "
                    f"(in-window race); MessageQueue row preserved as audit/record; "
                    f"KNOWN LIMITATION: message in narrow race window may not be "
                    f"delivered on resume. Follow-up: materialize "
                    f"_deferred_question_pause to DB (instances.pause_pending)."
                )
            else:
                task = Task(
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id=instance_id,
                    message_id=message_id,
                    status=TaskStatus.PENDING.value,
                    created_at=datetime.now(timezone.utc),
                    is_deferred=is_deferred,
                    is_background=is_background,
                    # ``work_id`` is the linkage handle for the
                    # JobItem/Task pair (POC path) or a fresh UUID minted
                    # earlier in this method (legacy path). Always non-None
                    # at this point — see the early auto-generation
                    # immediately above this block. Passing it explicitly
                    # ensures ``task.work_id`` matches the value the caller
                    # intended (``enqueue_message_job``'s shared UUID); if
                    # we relied on ``default_factory`` alone the Task row
                    # would mint an unrelated UUID and the linkage contract
                    # (JobItem.job_id == Task.work_id) would be silently
                    # broken.
                    work_id=work_id,
                )
                session.add(task)
                if (
                    instance_for_pause_guard is not None
                    and instance_for_pause_guard.status
                    == InstanceStatus.PAUSED.value
                ):
                    logger.info(
                        f"instance_messaging: PROCESS_MESSAGE Task created for "
                        f"instance {instance_id[:8]}... with DB=PAUSED; relying "
                        f"on claim_pending_task SQL gate to defer until resume"
                    )
            # ``task.work_id`` was either inherited from the caller
            # (``enqueue_message_job``'s shared UUID, satisfying the
            # linkage contract with JobItem.job_id) or minted above.
            # No re-capture is needed; the local ``work_id`` already holds
            # the correct value even when the marker intentionally skips Task.

            # 3. Update instance status to RUNNING for any state that is
            #    NOT already RUNNING and NOT PAUSED. A terminal instance
            #    (COMPLETED / TERMINATED / ERROR / FAILED) is reactivated on
            #    a new message — "terminal" only records WHY the last run
            #    stopped; the checkpoint, message history, and LangGraph
            #    thread all persist in the DB and reload on the next
            #    graph.astream, so reviving a terminated instance is the
            #    same machinery as reviving a completed one (revive-fix,
            #    2026-07-01). PAUSED is intentionally excluded here — the
            #    messages endpoint routes pause through the explicit resume
            #    path; enqueue itself must not flip PAUSED so the
            #    cooperative pause gate (claim_pending_task excludes paused
            #    instances) and the resume cascade stay in control.
            instance = session.get(Instance, instance_id)
            if instance:
                instance_agent_id = instance.agent_id
                previous_status = instance.status
                is_terminal_revival = previous_status in (
                    InstanceStatus.COMPLETED.value,
                    InstanceStatus.TERMINATED.value,
                    InstanceStatus.ERROR.value,
                    InstanceStatus.FAILED.value,
                )
                if instance.status in (
                    InstanceStatus.IDLE.value,
                    InstanceStatus.WAITING_CHILDREN.value,
                ) or is_terminal_revival:
                    instance.status = InstanceStatus.RUNNING.value
                    status_changed_to_running = True
                    is_idle_to_running = previous_status == InstanceStatus.IDLE.value
                    if is_terminal_revival:
                        suffix = f" ({path_label})" if path_label else ""
                        logger.info(
                            f"Reactivating terminal instance {instance_id[:8]}... "
                            f"(was {previous_status}) for new message{suffix}"
                        )
                instance.last_activity_at = datetime.now(timezone.utc)
                instance.version = (instance.version or 1) + 1
            else:
                logger.warning(
                    f"Instance {instance_id} not found in database during message "
                    f"enqueue. This may indicate the instance was not properly persisted."
                )

            # 4. Create MESSAGE_RECEIVED event for event-sourced features
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
            # Capture the Task PK after commit + refresh so the caller
            # can surface it as ``AsyncMessageResult.job_id``. The marker
            # branch intentionally has no Task and leaves task_id=None.
            if task is not None:
                # ``task.id`` is populated by the autoincrement; refresh()
                # re-reads the row from the DB to pick it up.
                try:
                    session.refresh(task)
                    task_id = task.id
                except Exception as e:
                    # Should not happen — the insert succeeded (we're past
                    # commit). Log and continue with None so callers degrade
                    # gracefully (HTTP route doesn't read task_id).
                    logger.warning(
                        f"Failed to refresh Task row for message {message_id}: {e}"
                    )
                    task_id = None

        return _PreparedEnqueueContext(
            message_id=message_id,
            msg_type=msg_type,
            status_changed_to_running=status_changed_to_running,
            is_idle_to_running=is_idle_to_running,
            instance_agent_id=instance_agent_id,
            previous_status=previous_status,
            task_id=task_id,
            work_id=work_id,
            is_deferred=is_deferred,
        )

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
    ) -> "AsyncMessageResult":
        """Enqueue a message via the unified dispatcher.

        All messages flow through the same single dispatcher:

          1. ``_prepare_enqueued_message`` writes ``MessageQueue`` + ``Task``
             rows in a single transaction.
          2. ``worker_pool.notify_work()`` wakes a worker to claim the Task.

        No ``JobItem`` (job_queue_items) row is ever created for a message.
        This eliminates the dual-record coupling that caused the
        06f500af-class bugs.

        ``AsyncMessageResult.job_id`` is set to ``task.work_id`` (the
        stable cross-system UUID4 handle introduced in Phase 1, Batch
        3 of ``feature/virtual-job-management-surface``). The HTTP
        route discards ``job_id``; the ``job_continue`` tool returns it
        as ``new_job_id``. This supersedes the prior ``str(task_id)``
        adapter — the int PK was a stop-gap until ``work_id`` was
        added; the resolver now resolves ``work_id`` uniformly across
        ``task`` and ``job_queue_items``.

        ``is_deferred`` (Phase 3 Part B1, 2026-06-27): keyword-only
        marker that stamps the created Task row with
        ``Task.is_deferred=True``. The worker pool's idle gate holds
        the task until every non-defer queue is empty. Default False
        preserves the prior behaviour for every caller that does not
        opt in (HTTP route, telegram, scheduler, internal reports).
        Keyword-only on purpose — it is a forward-looking orchestrator
        affordance and threading it positionally would silently
        re-route existing traffic if a caller miscounted args.

        New-message-during-pause behaviour:

            When this method is called for a PAUSED instance, the
            ``_prepare_enqueued_message`` helper writes a fresh ``Task``
            row in PENDING status. The pause-gate in
            ``TaskRepository.claim_pending_task`` excludes PAUSED instances
            from worker claim. INTENDED BEHAVIOUR: messages queue in
            PENDING and are claimed the moment the instance resumes.
        """
        # Wrap the sync DB prelude in asyncio.to_thread so the session.commit()
        # inside `_prepare_enqueued_message` cannot block the event loop. Under
        # SQLite WAL write contention (busy_timeout=30s) a sync commit on the
        # event loop thread would wedge the loop completely — Ctrl+C ignored,
        # all APIs frozen. See the deadlock analysis in the experience docs.
        ctx = await asyncio.to_thread(
            self._prepare_enqueued_message,
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

        # Phase 5 (Option B): when this message is being delivered via
        # the JobProcessor's message branch, ``ctx.work_id`` is the
        # shared UUID linking the Task ↔ JobItem. Stamp the
        # ``message_id`` onto the JobItem mirror so the cross-system
        # guard in ``claim_pending_task`` can correlate active MESSAGE
        # JobItems with their ``message_queue`` row. Failure is
        # non-fatal (same pattern as JobProcessor L1059-1069).
        if ctx.work_id:
            try:
                await asyncio.to_thread(
                    self._manager._job_queue_service._repository.stamp_message_id,
                    ctx.work_id,
                    ctx.message_id,
                )
            except Exception:
                logger.debug(
                    f"enqueue_message: stamp_message_id failed for "
                    f"work_id={ctx.work_id[:8]}...",
                    exc_info=True,
                )

        # Emit status_change event if status was changed to running
        if ctx.status_changed_to_running:
            await self._manager._live_hub.stream_status_change(
                instance_id, InstanceStatus.RUNNING.value, agent_id=ctx.instance_agent_id
            )

        # Trigger title generation for first message (fire-and-forget)
        # This fires when instance transitions from IDLE -> RUNNING with any message type
        self._maybe_trigger_title_generation(
            instance_id, message, ctx.is_idle_to_running
        )

        # Unified dispatch: notify the WorkerPool (Task row was already
        # written in the prelude, in the same transaction as the
        # MessageQueue row). No path-specific branch — the legacy
        # ``_job_queue_service.enqueue()`` call was eliminated in D13.
        if self._manager._worker_pool is not None:
            self._manager._worker_pool.notify_work()

        # ``job_id`` payload: ``task.work_id`` (UUID4) is the stable
        # cross-system handle minted by the Task model's
        # ``default_factory``. The HTTP ``send_message`` route discards
        # ``job_id`` entirely; the ``job_continue`` tool surfaces it as
        # ``new_job_id`` to the calling agent — both work because the
        # UUID4 is universally unique and the resolver
        # (``daemon.services.work_resolver``) accepts it on both the
        # ``task`` and ``job_queue_items`` sides of the union.
        # ``work_id`` is always populated by the Task model's
        # ``default_factory`` (NOT NULL on the column), so no fallback
        # is needed.
        job_id = ctx.work_id

        logger.debug(
            f"Enqueued message {ctx.message_id} for instance {instance_id} "
            f"task_id={job_id}"
        )

        return AsyncMessageResult(
            message_id=ctx.message_id,
            instance_id=instance_id,
            status="queued",
            job_id=job_id,
        )

    @property
    def _job_repository(self) -> Any:
        """Access JobRepository through the manager's JobQueueService.

        Resolves to ``manager._job_queue_service._repository``. Returns
        ``None`` when the JobQueueService has not been wired yet (test
        fixtures that build ``InstanceManager`` directly without
        ``api.py`` lifespan). Callers MUST handle ``None`` gracefully —
        the POC ``enqueue_message_job`` skips JobItem creation when the
        repo is unavailable so the legacy ``enqueue_message`` path
        remains the fallback.
        """
        try:
            service = self._manager._job_queue_service
        except AttributeError:
            return None
        if service is None:
            return None
        return getattr(service, "_repository", None)

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
    ) -> "AsyncMessageResult":
        """Submit a message to the queue as a JobItem (Option B).

        Option B (synchronous Task contract): the ``MessageQueue`` and
        ``Task`` rows are created synchronously (via
        :meth:`_prepare_enqueued_message`) BEFORE the JobItem is
        enqueued, so the HTTP response can carry a real ``message_id``
        immediately. The ``JobProcessor._process_next_job`` message
        branch is then reduced to a wake-only step — it just calls
        ``worker_pool.notify_work()`` to surface the already-existing
        Task to a worker thread (the Task is created in PENDING by
        ``_prepare_enqueued_message`` and the worker pool's claim path
        picks it up).

        Architecture:
            1. Resolve the target instance's project_id + queue_id
               (cross-project guard, default ``system_parallel_queue``).
            2. Mint one UUID and call
               ``_prepare_enqueued_message(work_id=job_id, ...)`` to write
               the ``MessageQueue`` + ``Task`` rows in one transaction.
            3. Restore the synchronous RUNNING SSE and first-message title
               side effects after the transaction commits.
            4. Call ``JobQueueService.enqueue(job_type='message',
               instance_id=instance_id, job_id=job_id, ...)``. This creates
               the JobItem with the exact shared UUID and only then emits
               ``dispatch_bus.notify_new_job()`` to wake the
               ``JobProcessor``.
            5. Stamp the authoritative ``message_id`` onto the JobItem and
               return ``AsyncMessageResult`` with the real message ID,
               ``status='queued'``, and ``job_id == Task.work_id``.

        Concurrency: ``concurrency_limit`` on the resolved queue is
        NOW ENFORCED for messages. With a FIFO queue at
        ``concurrency_limit=1``, two messages to the same/different
        instances run strictly serially.

        Failure handling:
            * Any exception while resolving the queue or creating the Task
              propagates before a JobItem is visible to the dispatch bus.
            * If JobItem creation fails after the Task transaction commits,
              the Task remains the authoritative work item and the caller
              receives the enqueue error for normal recovery handling.

        Args:
            instance_id: Target instance ID (the existing instance that
                will receive the message).
            message: User content.
            source: Source tag (e.g. ``"api"``, ``"telegram:user:1"``).
            priority: 0=system, 1=user.
            images: Optional base64 images for vision messages.
            metadata: Optional metadata dict.
            is_deferred: Stamps ``Task.is_deferred=True`` on the
                created Task row so the worker pool's idle gate holds
                the task until every non-defer queue is empty.
            is_background: Stamps ``Task.is_background=True`` on the
                created Task row for background-queue routing.
            queue_id: Optional queue override. Validated against the
                target project; falls back to ``system_parallel_queue``
                on mismatch.

        Returns:
            ``AsyncMessageResult`` with the real ``message_id`` (Task
            row's column), ``instance_id=instance_id``,
            ``status='queued'`` (waiting for slot), and ``job_id``
            populated as the JobItem's UUID4 (== ``Task.work_id``).
        """
        # --- Step 1: Resolve queue_id (reuse existing logic) ---
        # We need the instance's project_id (authoritative
        # ``instances.project_id`` column — NOT the LLM-controllable
        # ``instance_metadata.project_id``) and the resolved queue_id
        # (cross-project guard, default ``system_parallel_queue``).

        # 1a. Resolve project_id from the instance row.
        project_id_for_job: str | None = None
        instance_meta = None
        raw_project_was_none = True
        try:
            instance_meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if instance_meta is not None:
                raw_project_id = instance_meta.project_id
                if raw_project_id is not None:
                    raw_project_was_none = False
                project_id_for_job = normalize_project_id(raw_project_id)
        except Exception as project_err:
            logger.debug(
                f"enqueue_message_job: failed to resolve project_id "
                f"for instance {instance_id[:8]}...: "
                f"{type(project_err).__name__}: {project_err}"
            )

        if project_id_for_job is None:
            logger.warning(
                "Instance %s has no project_id; queue routing will use default",
                instance_id,
            )

        # 1b. Resolve agent_id from the instance (for JobItem row).
        agent_id_for_job = (
            instance_meta.agent_id if instance_meta is not None else None
        ) or "default"

        # 1b'. Resolve agent_tag from the instance when available.
        # Older Instance rows may not have ``agent_tag`` set — use
        # ``getattr`` with a default of None so the registry falls
        # back to the base metadata in that case.
        agent_tag_for_job = (
            getattr(instance_meta, "agent_tag", None)
            if instance_meta is not None
            else None
        )

        # 1c. Resolve queue_id (cross-project guard).
        queue_id_for_job: str | None = None
        queue_repo = getattr(
            getattr(self._manager, "_job_queue_service", None),
            "_queue_repo",
            None,
        )
        queue_id_supplied = bool(queue_id and queue_id.strip())
        if project_id_for_job is not None:
            if queue_repo is not None:
                try:
                    if queue_id_supplied:
                        try:
                            requested = await asyncio.to_thread(
                                queue_repo.get, queue_id
                            )
                        except Exception as get_err:
                            logger.warning(
                                "enqueue_message_job: queue_repo.get "
                                f"failed for queue_id={queue_id!r} "
                                f"on project {project_id_for_job}: "
                                f"{type(get_err).__name__}: {get_err}; "
                                "falling back to default "
                                "system_parallel_queue"
                            )
                            requested = None
                        if (
                            requested is not None
                            and getattr(requested, "project_id", None)
                            == project_id_for_job
                        ):
                            queue_id_for_job = requested.queue_id
                        else:
                            mismatch_reason = (
                                "not_found_or_repo_error"
                                if requested is None
                                else "wrong_project"
                            )
                            logger.warning(
                                "enqueue_message_job: caller-supplied "
                                f"queue_id={queue_id!r} is invalid "
                                f"({mismatch_reason}) for project "
                                f"{project_id_for_job}; falling back "
                                "to default system_parallel_queue"
                            )
                    if queue_id_for_job is None:
                        try:
                            queue = await asyncio.to_thread(
                                queue_repo.get_by_name,
                                project_id_for_job,
                                "system_parallel_queue",
                            )
                        except Exception as by_name_err:
                            logger.warning(
                                "enqueue_message_job: queue_repo."
                                "get_by_name failed for project "
                                f"{project_id_for_job}: "
                                f"{type(by_name_err).__name__}: "
                                f"{by_name_err}; leaving queue_id "
                                "unset on the JobItem"
                            )
                            queue = None
                        if queue is not None:
                            queue_id_for_job = queue.queue_id
                except Exception as queue_lookup_err:
                    logger.debug(
                        f"enqueue_message_job: unexpected error "
                        f"resolving queue_id for project "
                        f"{project_id_for_job}: "
                        f"{type(queue_lookup_err).__name__}: "
                        f"{queue_lookup_err}"
                    )

        # --- Step 2: Mint the shared linkage ID and create the Task +
        # MessageQueue rows FIRST. The JobItem must not be visible to the
        # dispatch bus until its authoritative Task already exists.
        job_id = str(uuid.uuid4())
        ctx = await asyncio.to_thread(
            self._prepare_enqueued_message,
            instance_id=instance_id,
            message=message,
            source=source,
            priority=priority,
            images=images,
            metadata=metadata,
            is_deferred=is_deferred,
            is_background=is_background,
            work_id=job_id,
        )

        # Preserve the historical synchronous side effects from the
        # enqueue path: publish the RUNNING transition and start title
        # generation only after the message transaction has committed.
        if ctx.status_changed_to_running:
            await self._manager._live_hub.stream_status_change(
                instance_id, InstanceStatus.RUNNING.value, agent_id=ctx.instance_agent_id
            )
        self._maybe_trigger_title_generation(
            instance_id, message, ctx.is_idle_to_running
        )

        # --- W7 FIX (orphaned JobItem guard, 2026-07) ---
        # When the Phase 2 marker guard in ``_prepare_enqueued_message``
        # fires, the ``MessageQueue`` row is created (durable audit
        # record) but the ``Task`` row is SKIPPED to prevent
        # ``WorkerPool.claim_pending_task`` from claiming a spurious graph
        # turn during the cascade's DB-commit window. ``ctx.task_id`` is
        # ``None`` in that branch. Without this guard, the JobItem
        # creation below would enqueue an item that has NO Task to
        # claim — the JobProcessor would wake the dispatch bus, try to
        # surface a Task that doesn't exist, and the work would be
        # silently lost.
        #
        # We log a WARNING (the same level used elsewhere in this path
        # for skip events) and skip both the JobItem creation and the
        # downstream ``queued`` snapshot / message_id stamp. The
        # ``MessageQueue`` row remains in READY state for later
        # inspection; the narrow-window message may be lost (a known
        # limitation tracked under the C2 follow-up).
        if ctx.task_id is None:
            logger.warning(
                f"enqueue_message_job: SKIPPING JobItem creation for "
                f"instance {instance_id[:8]}... — reason=marker_guard "
                f"(Phase 2 deferred-pause race guard skipped the Task "
                f"row in _prepare_enqueued_message; MessageQueue "
                f"{ctx.message_id[:8]}... preserved as audit record)"
            )
            return AsyncMessageResult(
                message_id=ctx.message_id,
                instance_id=instance_id,
                status="queued",
                job_id=job_id,
                queued=False,
            )

        # --- Step 3: Enqueue the JobItem using the exact same UUID. ---
        # JobQueueService.enqueue emits the dispatch-bus notification only
        # after this call returns, so the JobProcessor can never observe a
        # message JobItem before its Task + MessageQueue rows exist.
        await self._manager._job_queue_service.enqueue(
            agent_id=agent_id_for_job,
            message=message,
            source=source,
            project_id=project_id_for_job,
            priority=priority,
            metadata={
                **(metadata or {}),
                "images": images or [],
                "is_deferred": is_deferred,
                "is_background": is_background,
            },
            queue_id=queue_id_for_job,
            job_type="message",
            instance_id=instance_id,
            agent_tag=agent_tag_for_job,
            job_id=job_id,
        )

        # Snapshot queue capacity synchronously after the JobItem exists. The
        # newly-created item is still in the ``queued`` admission bucket, so it
        # is deliberately excluded from ``active_count``. This avoids relying
        # on the JobProcessor's later queued -> active claim timing.
        queued = False
        if queue_repo is None or queue_id_for_job is None:
            logger.warning(
                "enqueue_message_job: unable to snapshot queue capacity for "
                "job %s (queue repository or queue_id unavailable); "
                "defaulting queued=False",
                job_id[:8],
            )
        else:
            try:
                queue = await asyncio.to_thread(queue_repo.get, queue_id_for_job)
                concurrency_limit = (
                    getattr(queue, "concurrency_limit", None)
                    if queue is not None
                    else None
                )
                if concurrency_limit is None:
                    logger.warning(
                        "enqueue_message_job: queue %s missing or has no "
                        "concurrency_limit; defaulting queued=False for job %s",
                        queue_id_for_job,
                        job_id[:8],
                    )
                else:
                    admission_counts = await asyncio.to_thread(
                        queue_repo.count_jobs_by_admission,
                        queue_id_for_job,
                    )
                    active_count = int(admission_counts.get("active", 0))
                    queued = active_count >= int(concurrency_limit)
            except Exception as capacity_err:
                logger.warning(
                    "enqueue_message_job: failed to snapshot queue capacity "
                    "for queue %s and job %s: %s: %s; defaulting queued=False",
                    queue_id_for_job,
                    job_id[:8],
                    type(capacity_err).__name__,
                    capacity_err,
                )
                queued = False

        # Stamp the message_id onto the JobItem for cross-system correlation.
        # This remains best-effort for compatibility with the historical path:
        # Task + MessageQueue creation and queue admission have already succeeded.
        try:
            await asyncio.to_thread(
                self._manager._job_queue_service._repository.stamp_message_id,
                job_id,
                ctx.message_id,
            )
        except Exception:
            logger.debug(
                f"enqueue_message_job: stamp_message_id failed for job "
                f"{job_id[:8]}...",
                exc_info=True,
            )

        # Do not notify the WorkerPool here. The JobProcessor's message
        # branch is the wake-only handoff after queue slot admission;
        # waking here would bypass that gate.
        # W2 fix — project-less fallback. When the authoritative instance project
        # was None, JobQueueService.enqueue already called notify_new_job with
        # the normalized system project (which works today), but this belt-and-suspenders
        # fallback calls notify_all() to guarantee the wakeup reaches every known
        # project + the global event. Safe getattr keeps it inert when the bus
        # isn't wired (e.g. unit tests with a bare MagicMock manager).
        if raw_project_was_none:
            bus = getattr(
                getattr(self._manager, "_job_queue_service", None),
                "_dispatch_bus",
                None,
            )
            if bus is not None and hasattr(bus, "notify_all"):
                try:
                    bus.notify_all()
                except Exception as bus_err:
                    logger.debug(
                        f"enqueue_message_job: notify_all fallback failed "
                        f"for project-less instance {instance_id[:8]}...: "
                        f"{type(bus_err).__name__}: {bus_err}"
                    )
        return AsyncMessageResult(
            message_id=ctx.message_id,
            instance_id=instance_id,
            status="queued",
            job_id=job_id,
            queued=queued,
        )

    async def _process_message_with_tracking(
        self, 
        instance_id: str, 
        message: str,
        message_id: str,
        cancellation_token: CancellationToken | None = None,
        is_retry: bool = False,
        retry_count: int = 0,
        message_source: str | None = None,
        images: list[str] | None = None,
        silent: bool = False,
    ) -> "MessageResult":
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
            images: Optional list of base64-encoded images for multimodal content.
            silent: If True, resume from checkpoint without injecting any message.

        Returns:
            MessageResult with response data.
            
        Raises:
            OperationCancelledError: If cancellation is requested.
        """
        from ..manager import MessageResult

        # ── <meta> tag parsing (parent-dispatch only) ────────────
        # Strip ``<meta>...</meta>`` control blocks ONLY when the
        # message came from a parent agent dispatching to this child
        # worker — i.e. ``message_source`` starts with
        # ``internal_agent:`` and is NOT a job-event ping
        # (``internal_agent:job_event:``). User / API / telegram /
        # ``internal_report:`` / ``internal_error_report:`` / None
        # all pass through untouched: stripping their tags would
        # leak control-plane syntax into the user-visible message
        # and create a hijack surface where a child LLM's stray
        # ``<meta>`` could mutate the parent's skill set.
        #
        # The carve-out mirrors the inverse of the C3
        # ``is_completion_report`` carve-out below — same prefixes,
        # opposite selection (parent dispatch vs. internal pings).
        # ``_meta_skill`` stays ``None`` for non-parent sources and
        # is consumed by the C3 block further down.
        _meta_skill: str | None = None
        _is_parent_dispatch = (
            message_source is not None
            and message_source.startswith("internal_agent:")
            and not message_source.startswith("internal_agent:job_event:")
        )
        if _is_parent_dispatch and message and isinstance(message, str):
            message, _meta = parse_meta_tag(message)
            _meta_skill = extract_load_skill(_meta)
            if _meta_skill is not None:
                logger.info(
                    f"[MetaTag] Extracted load_skill='{_meta_skill}' "
                    f"for instance {instance_id[:8]}..."
                )

        # Get instance graph (will lazy-load from DB if needed)
        # Note: get_instance() now handles MCP preload internally
        graph = await self._manager.get_instance(instance_id)
        
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
            # Base recursion limit; overridden below once the agent
            # metadata is resolved so per-agent multipliers apply.
            "recursion_limit": self._config.limits.graph_recursion_limit,
        }
        
        # Variables for checkpoint-based streaming
        final_content = ""
        last_ai_message = None
        
        # Determine the effective source for progressive dispatch
        dispatch_source: str | None = None
        if message_source:
            # C1 fix: Only treat internal_report:* and internal_error_report:* as completion reports.
            # internal_agent:* is agent-to-agent communication, NOT a completion report,
            # so it must NOT trigger original_source lookup/replacement.
            is_internal_report = (
                message_source.startswith("internal_report:") or
                message_source.startswith("internal_error_report:")
            )
            if is_internal_report:
                # This is an internal message (completion report, error report, etc.)
                # Retrieve the original external source from instance metadata.
                # Wrap the sync DB read in ``asyncio.to_thread`` to keep the
                # event loop responsive (see deadlock analysis in experience docs).
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
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
                # Wrap the sync DB read in ``asyncio.to_thread`` for the same
                # event-loop responsiveness reason as above.
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
                if instance_meta is not None and instance_meta.instance_metadata is not None:
                    current = instance_meta.instance_metadata.get("original_source")
                    if not current and not message_source.startswith("internal_"):
                        logger.debug(f"[DISPATCH] storing original_source: instance={instance_id}, source={message_source}, current={current}")
                        # Sync DB write — wrap in ``asyncio.to_thread``.
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id, "original_source", message_source,
                        )
                    else:
                        logger.debug(f"[DISPATCH] original_source already set: instance={instance_id}, current={current}, skipping source={message_source}")
                else:
                    # Instance metadata doesn't exist yet, set it directly
                    if not message_source.startswith("internal_"):
                        logger.debug(f"[DISPATCH] storing original_source: instance={instance_id}, source={message_source}, current=None")
                        # Sync DB write — wrap in ``asyncio.to_thread``.
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id, "original_source", message_source,
                        )

        # Compute ``is_completion_report`` once at the top of the method.
        # It's consumed in two places:
        #   1. The project-context gate below (skip injection when an
        #      internal completion/error/agent report is the source).
        #   2. The meta-tag REPLACE gate further down (Fix 1+2) — must
        #      be in scope on both retry and non-retry paths because
        #      the REPLACE block runs unconditionally.
        is_completion_report = (
            message_source is not None and (
                message_source.startswith("internal_report:") or
                message_source.startswith("internal_error_report:") or
                message_source.startswith("internal_agent:job_event:")
            )
        )

        # Resolve the agent metadata ONCE at the top of the messaging
        # path so the ``assemble_context_messages()`` orchestrator call
        # further down has the versioned (vs base) agent metadata in
        # hand. The orchestrator consults it for the agent's skill
        # injection flags / team membership / tools allowlist — keeping
        # a single resolve here avoids re-resolving inside
        # ``agent_node`` and guarantees the versioned meta is honoured
        # for v2 / tagged agents.
        #
        # Cheap lookup — the registry is in-memory; only the registry
        # cache miss case hits disk. ``None`` is treated as the
        # default for any caller that doesn't have a resolvable
        # agent_id.
        #
        # Resolution reuses :meth:`_resolve_agent_meta_from_row` so the
        # versioned (``get_version`` → ``get_resolved``) fallback lives
        # in one place (S2 fix preserved by the helper).
        _messaging_agent_meta: Any | None = None
        try:
            _instance_row_for_meta = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            _messaging_agent_meta = self._resolve_agent_meta_from_row(
                _instance_row_for_meta
            )
        except Exception as _meta_exc:  # pragma: no cover - defensive
            logger.debug(
                f"[Messaging] Failed to resolve agent_meta for "
                f"{instance_id[:8]}...: {_meta_exc}"
            )
            _messaging_agent_meta = None

        # Apply the per-agent recursion-limit override / multiplier now
        # that the agent metadata is resolved. ``config`` is not
        # consumed until the astream call below, so updating it here is
        # safe and lets long-running working agents (e.g. worker, coder)
        # exceed the global step quota. Reuses the already-resolved
        # ``_messaging_agent_meta`` (no second registry lookup).
        config["recursion_limit"] = self._resolve_recursion_limit_for_meta(
            _messaging_agent_meta
        )

        # ── Hybrid Context Injection (2026-07-29) ─────────────────────────
        # Capture the once-per-instance ``project_injected`` flag from
        # the DB BEFORE any of the project / shared-context injection
        # writes below flip it. The captured value drives
        # :func:`daemon.services.context_messages.assemble_context_messages`
        # later in this function: ``False`` ⇒ build the persistent
        # project + shared-context block and prepend it to
        # ``graph_input``; ``True`` ⇒ skip the persistent builders
        # (the orchestrator emits only ephemeral skills) because the
        # persistent block was already checkpointed on the first
        # turn.
        #
        # Read the DB once at the top — works for first attempt
        # (captures pre-injection state) and retry (captures the
        # post-first-attempt state, which is ``True`` on the happy
        # path). ``try/except`` guards against transient DB errors
        # so a failed lookup falls through to ``False`` — the safe
        # "build persistent" default. This single read is the only
        # extra DB call we add to the messaging path on the
        # steady-state hot path (every turn after the first the
        # orchestrator short-circuits, so the extra read is the cost
        # of correctness).
        project_already_injected = False
        # Whether a ``[SYSTEM CONTEXT: Auto-Load Skills]`` block is
        # currently checkpointed for this instance — gating the
        # ``<meta>`` REPLACE sweep (RemoveMessage) so it only targets an
        # id that actually exists (langgraph raises on an absent-id
        # RemoveMessage). Captured from the SAME instance-row read as
        # ``project_already_injected`` — no extra DB round-trip.
        auto_load_block_active = False
        try:
            _flag_row = await asyncio.to_thread(
                self._manager._instance_repository.get, instance_id
            )
            if _flag_row is not None and _flag_row.instance_metadata:
                project_already_injected = bool(
                    _flag_row.instance_metadata.get("project_injected")
                )
                auto_load_block_active = bool(
                    _flag_row.instance_metadata.get(AUTO_LOAD_BLOCK_ACTIVE_KEY)
                )
        except Exception as _flag_exc:  # pragma: no cover - defensive
            logger.debug(
                f"[Messaging] project_injected capture failed for "
                f"{instance_id[:8]}...: {_flag_exc}"
            )
            project_already_injected = False
            auto_load_block_active = False

        # Project context injection for first message only
        if not is_retry:
            if is_completion_report:
                # Skip project/shared-context injection for completion/error reports
                pass
            else:
                # ── Single read of the instance row (reused for both gates below) ──
                # Wrap the sync DB read in ``asyncio.to_thread`` (see deadlock
                # analysis in experience docs). Both the project-context and
                # shared-context gates read the same row, so one fetch covers
                # both — avoids the double round-trip a previous revision paid.
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
                # Snapshot for repeated gate checks (``instance_meta`` may be
                # detached after the next ``to_thread`` round-trip that writes
                # back to it via ``set_metadata``).
                instance_metadata = (
                    instance_meta.instance_metadata
                    if instance_meta is not None and instance_meta.instance_metadata
                    else None
                )

                # ── Project context injection (existing logic) ─────────────────
                # Context Injection Restructure: per-turn project + KV +
                # notes + history content is rebuilt inside ``agent_node``
                # by :func:`daemon.services.context_messages.assemble_context_messages`
                # → :func:`build_project_context_message`. The legacy
                # project-context body builder is no longer prepended
                # to the user message here — the per-turn orchestrator
                # owns the only delivery path.

                if not project_already_injected:
                    # First injection → attempt project injection
                    existing_project_id = None
                    if instance_metadata:
                        existing_project_id = instance_metadata.get("project_id")

                    injection_succeeded = False

                    if existing_project_id:
                        # project_id exists (inherited from parent) → record
                        # the linkage so ``assemble_context_messages`` can pick
                        # the project up per-turn. The actual project-context
                        # content is rebuilt inside ``agent_node`` by
                        # :func:`daemon.services.context_messages.assemble_context_messages`
                        # → :func:`build_project_context_message`.
                        matched_project = await asyncio.to_thread(
                            self._project_repository.get, existing_project_id
                        )
                        if matched_project:
                            injection_succeeded = True
                            logger.info(f"Project context injection: using stored project_id '{existing_project_id}' for instance {instance_id[:8]}...")
                    else:
                        # No project_id yet → extract keywords and try to match.
                        # ``extract_project_keywords`` and the
                        # ``match_by_keywords`` repository call are still
                        # required: they may stamp a project_id onto the
                        # instance metadata that the per-turn
                        # ``assemble_context_messages`` builder needs.
                        from ..manager import extract_project_keywords
                        keywords = extract_project_keywords(message)

                        if keywords:
                            # Wrap the sync ``match_by_keywords`` DB read in
                            # ``asyncio.to_thread``.
                            matched_project = await asyncio.to_thread(
                                self._project_repository.match_by_keywords, keywords
                            )

                            if matched_project:
                                # Log the match
                                logger.info(
                                    f"Project context injection: matched '{matched_project.name}' "
                                    f"from keywords: {keywords[:5]}..."
                                )

                                injection_succeeded = True

                                # Update instance metadata with project_id.
                                # Sync DB write — wrap in ``asyncio.to_thread``.
                                # Stamped on the instance metadata so
                                # ``assemble_context_messages`` can pick the
                                # project up per-turn. The project_id is
                                # the same gate the legacy prepend used —
                                # preserving it here keeps the matching
                                # behavior consistent.
                                await asyncio.to_thread(
                                    self._manager._instance_repository.set_metadata,
                                    instance_id, "project_id", matched_project.project_id,
                                )

                                logger.debug(f"Injected project context for instance {instance_id[:8]}...")
                    
                    # Mark as injected to prevent re-injection on subsequent messages.
                    # Sync DB write — wrap in ``asyncio.to_thread``.
                    if injection_succeeded:
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id, "project_injected", True,
                        )

                # ── Shared context metadata injection (Option C) ──────
                # The shared-context KV block is now part of the
                # ``[SYSTEM CONTEXT: Related Project]`` HumanMessage
                # built per-turn inside ``agent_node`` by
                # :func:`daemon.services.context_messages.assemble_context_messages`
                # → :func:`build_project_context_message` →
                # :func:`_format_kv_metadata_section`. Prepending the
                # KV block to the user message body here would
                # double-inject; the per-turn builder is the only
                # source of truth.
                #
                # The once-per-instance ``shared_context_injected``
                # flag (set by the legacy path) is now redundant — the
                # orchestrator reads ``project_injected`` instead and
                # the per-turn builder runs every turn. We leave the
                # metadata key in the schema for backward compatibility
                # with pre-existing rows but never write or read it
                # here.

            # ── Skill Injection (Phase 3: dynamic skill evolution) ──
            # Runs only on first attempt (``if not is_retry:`` above).
            # Skipped for completion reports — those are internal pings,
            # not real user messages, and the resolver doesn't need skill
            # context for them. Gated on ``agent_meta.skill_injection``
            # so opt-in agents control the cost of the search.
            #
            # The injection service is looked up via ``getattr(..., None)``
            # so a manager built without ``skill_evolution`` config (or
            # before Phase 3 wired it in) degrades to a no-op rather than
            # raising ``AttributeError``. The whole block is wrapped in
            # ``try/except`` so a transient DB / search error leaves the
            # user message path intact — the graph still runs with just
            # the bare ``content`` field.
            if not is_completion_report:
                try:
                    # Re-fetch instance metadata — by now the project
                    # injection block above may have stamped a new
                    # ``project_id`` onto it.
                    skill_instance_meta = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    if skill_instance_meta is not None:
                        from ..registry import get_registry
                        registry = get_registry()
                        # C1 fix: thread the instance's bound ``agent_tag``
                        # so the versioned (not base) meta's
                        # ``skill_injection`` flag wins for v2/etc.
                        # callers. Same ``get_version() or
                        # get_resolved()`` fallback pattern used by
                        # ``_apply_tool_filter`` and
                        # ``_check_team_membership``. ``getattr`` with
                        # ``None`` default keeps tests using
                        # ``SimpleNamespace``-style instance_meta
                        # compatible.
                        agent_meta = (
                            registry.get_version(
                                skill_instance_meta.agent_id,
                                getattr(
                                    skill_instance_meta, "agent_tag", None
                                ),
                            )
                            or registry.get_resolved(
                                skill_instance_meta.agent_id
                            )
                        )

                        if agent_meta and getattr(
                            agent_meta, "skill_injection", False
                        ):
                            skill_project_id: str | None = None
                            if skill_instance_meta.instance_metadata:
                                skill_project_id = (
                                    skill_instance_meta.instance_metadata.get(
                                        "project_id"
                                    )
                                )

                            # ── Clone-on-miss (Phase 4) ──────────────────
                            # Ensure all agent skills exist in project
                            # scope BEFORE BM25 search runs. This makes
                            # freshly-cloned templates discoverable to
                            # SkillSearchService on the very first
                            # injection. Cloning is idempotent — existing
                            # skills are returned, not re-cloned.
                            clone_service = getattr(
                                self._manager,
                                "_skill_clone_service",
                                None,
                            )
                            if (
                                clone_service is not None
                                and skill_project_id is not None
                            ):
                                try:
                                    await clone_service.ensure_all_skills_async(
                                        agent_id=skill_instance_meta.agent_id,
                                        project_id=skill_project_id,
                                    )
                                except Exception as clone_exc:
                                    logger.warning(
                                        f"Clone-on-miss failed for "
                                        f"{instance_id[:8]}...: {clone_exc}"
                                    )

                            injection_service = getattr(
                                self._manager,
                                "_skill_injection_service",
                                None,
                            )
                            if injection_service is not None:
                                (
                                    injection_text,
                                    injected_skill_ids,
                                ) = await injection_service.inject_skills(
                                    message,
                                    skill_project_id,
                                    instance_id,
                                    message_id,
                                )
                                if injection_text:
                                    # the metrics service queries this
                                    # to attribute future feedback to
                                    # the skills that were offered.
                                    injection_service.track_injection(
                                        instance_id,
                                        message_id,
                                        injected_skill_ids,
                                    )
                                    # Context Injection Restructure —
                                    # Phase 3 / B2 fix: store the
                                    # skill-search result on the manager
                                    # so ``ContextSlot.assemble()``
                                    # (running inside ``agent_node``)
                                    # can reuse it on retry without
                                    # re-running the search (B3 fix).
                                    # Stored unconditionally — context is
                                    # always built per-turn, so the cost
                                    # is one extra dict entry per message.
                                    setter = getattr(
                                        self._manager,
                                        "set_context_skill_result",
                                        None,
                                    )
                                    if setter is not None:
                                        setter(
                                            instance_id,
                                            (injection_text, injected_skill_ids),
                                        )
                                else:
                                    # Search ran but yielded nothing.
                                    # Still store the empty result so a
                                    # retry of the same message does NOT
                                    # re-run the search (per B3). ``None``
                                    # here means "no injection text", not
                                    # "not searched" — the latter is the
                                    # absent-key case, which
                                    # ``assemble_context_messages`` treats
                                    # as "search again".
                                    setter = getattr(
                                        self._manager,
                                        "set_context_skill_result",
                                        None,
                                    )
                                    if setter is not None:
                                        setter(
                                            instance_id,
                                            (None, list(injected_skill_ids)),
                                        )
                                    # Persist injected skill IDs to instance
                                    # metadata so SkillMetricsService can
                                    # read them at task-completion time.
                                    if injected_skill_ids:
                                        try:
                                            await asyncio.to_thread(
                                                _dedup_merge_skill_ids,
                                                self._manager._instance_repository,
                                                instance_id,
                                                injected_skill_ids,
                                            )
                                        except Exception as e:
                                            logger.warning(
                                                f"Failed to persist "
                                                f"{INJECTED_SKILLS_METADATA_KEY} "
                                                f"for {instance_id[:8]}...: {e}"
                                            )
                except Exception as e:
                    logger.warning(
                        f"Skill injection failed for {instance_id[:8]}...: {e}"
                    )

        # ── C3 INVARIANT: Explicit <meta> injection runs FIRST (REPLACE
        # ── semantics). Auto_load DEDUP-MERGE runs SECOND (additive onto
        # ── the explicit set). This block is the explicit path — the
        # ── the auto_load side lives in ``instance_lifecycle.py`` and
        # ── honors the REPLACE by skipping any ``explicitly_replaced_ids``.
        #
        # Fix 1+2 gate: the REPLACE logic (skill injection + finalize
        # + metadata persist) is skipped when:
        #   * ``is_completion_report`` is True — a child agent's
        #     completion report that happens to contain a ``<meta>``
        #     tag must NOT hijack the parent instance's skill state.
        #   * ``is_retry`` is True — on retry the original message
        #     is re-processed with the same ``<meta>`` directive,
        #     which would create duplicate SUPERSEDED records.
        # The ``parse_meta_tag`` at the top of the method already
        # stripped ``<meta>...</meta>`` from ``message`` for parent
        # dispatches (``internal_agent:``-prefixed, non-job-event
        # sources). For other sources the message passes through
        # verbatim — including any literal ``<meta>...</meta>`` the
        # user typed — so ``_meta_skill`` stays ``None`` and this
        # entire C3 block is skipped naturally. Only the
        # authoritative REPLACE side-effects are gated.
        #
        # Key difference from the first-attempt block above: REPLACE
        # ``last_injected_skill_ids`` instead of dedup-merge. ``<meta>``
        # is the authoritative skill directive for this message and any
        # previously-injected skills that are NOT in the new set get a
        # ``SUPERSEDED`` usage record via ``finalize_superseded_skills``
        # so they stop skewing the completion-rate aggregation.
        # Declared here (before the meta block) so the REPLACE closure
        # can write via ``nonlocal`` and the persistent-context section
        # below can read it unconditionally — most messages have no
        # ``<meta>`` tag, so the variable must always be bound.
        _auto_load_sweep_agent_id: str | None = None
        if _meta_skill is not None:
            if is_completion_report or is_retry:
                logger.debug(
                    "Skipping <meta> tag REPLACE (completion_report=%s, "
                    "retry=%s) for instance %s",
                    is_completion_report, is_retry, instance_id[:8],
                )
            else:
                try:
                    # Resolve the instance row once for both project_id and
                    # agent_id below. Sync DB read — wrap in ``asyncio.to_thread``
                    # (same deadlock-avoidance pattern used elsewhere in
                    # this method).
                    _meta_instance = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    _meta_project_id: str | None = None
                    _meta_agent_id: str = (
                        getattr(_meta_instance, "agent_id", "") or ""
                        if _meta_instance is not None
                        else ""
                    )
                    if _meta_instance is not None and _meta_instance.instance_metadata:
                        _meta_project_id = (
                            _meta_instance.instance_metadata.get("project_id")
                        )

                    injection_service = getattr(
                        self._manager, "_skill_injection_service", None
                    )
                    # Pre-declare so the persist block below can read
                    # ``_meta_skill_ids`` even when the injection service
                    # isn't wired (older manager / pre-Phase 4 test
                    # fixtures).
                    _meta_skill_ids: list[str] = []
                    _meta_injection_text: str | None = None
                    # ``_auto_load_sweep_agent_id`` is declared above the
                    # meta block; the REPLACE closure sets it (nonlocal)
                    # when dropped skills invalidate the auto-load block.
                    if injection_service is not None:
                        (
                            _meta_injection_text,
                            _meta_skill_ids,
                        ) = await injection_service.inject_explicit_skill(
                            skill_name=_meta_skill,
                            project_id=_meta_project_id,
                            instance_id=instance_id,
                            message_id=message_id,
                            agent_id=_meta_agent_id,
                        )
                        # Context Injection Restructure — Phase 3 / Task 13:
                        # store the <meta>-tag skill result on the manager
                        # so ``ContextSlot.assemble()`` can rebuild the
                        # block with the unified ``[SYSTEM CONTEXT: Skills]``
                        # prefix in ``human_messages`` mode. Same pattern
                        # as the auto-search block above. Also store on
                        # the empty-text path so a retry of the same
                        # message does NOT re-run the explicit-skill
                        # resolver — mirrors the B3 short-circuit.
                        _meta_setter = getattr(
                            self._manager,
                            "set_context_skill_result",
                            None,
                        )
                        if _meta_setter is not None:
                            _meta_setter(
                                instance_id,
                                (
                                    _meta_injection_text,
                                    list(_meta_skill_ids),
                                ),
                            )
                        # Phase 4 metrics attribution. Same API the
                        # first-attempt block uses.
                        injection_service.track_injection(
                            instance_id, message_id, _meta_skill_ids
                        )

                    # C2 FIX — Finalize-on-Replace. If we have new IDs to
                    # stamp, compute the dropped set (anything previously
                    # tracked that isn't in the new set), then REPLACE
                    # ``last_injected_skill_ids`` (not merge). Skipping
                    # metadata persistence when the new set is empty keeps
                    # the existing checkpoint untouched — a ``<meta>`` tag
                    # that failed to resolve (skill not found) shouldn't
                    # erase the previously-injected set.
                    #
                    # Fix 4: the writes inside ``_finalize_and_replace``
                    # are reordered — metadata FIRST (atomic-ish, no
                    # orphan side effects if it fails), SUPERSEDED LAST
                    # (the only step with external side effects; the
                    # orphan-sweep task picks up partials).
                    if _meta_skill_ids:
                        def _finalize_and_replace(
                            _iid: str = instance_id,
                            _new_ids: list[str] = list(_meta_skill_ids),
                            _pid: str | None = _meta_project_id,
                            _aid: str = _meta_agent_id,
                        ) -> None:
                            nonlocal _auto_load_sweep_agent_id
                            inst = self._manager._instance_repository.get(_iid)
                            existing: list[str] = []
                            if inst is not None and inst.instance_metadata:
                                raw = inst.instance_metadata.get(
                                    INJECTED_SKILLS_METADATA_KEY
                                ) or []
                                if isinstance(raw, list):
                                    existing = [str(x) for x in raw if x]
                            new_set = {_new_id for _new_id in _new_ids if _new_id}
                            dropped = [
                                s for s in existing if s not in new_set
                            ]
                            # ── Fix 4: METADATA FIRST ─────────────────────
                            # REPLACE ``last_injected_skill_ids`` before any
                            # SUPERSEDED writes. If this fails, we abort
                            # cleanly — ``existing`` is still the source of
                            # truth and no orphan was created.
                            self._manager._instance_repository.set_metadata(
                                _iid,
                                INJECTED_SKILLS_METADATA_KEY,
                                list(new_set),
                            )
                            # Persist dropped IDs as
                            # ``explicitly_replaced_ids`` so the
                            # auto_load dedup-merge in
                            # ``instance_lifecycle.py`` skips them across
                            # checkpoint restores (Issue 2).
                            if dropped:
                                self._manager._instance_repository.set_metadata(
                                    _iid,
                                    REPLACED_SKILLS_METADATA_KEY,
                                    dropped,
                                )
                                # Flag the auto-load REMOVE sweep: the
                                # turn-1 checkpointed
                                # ``[SYSTEM CONTEXT: Auto-Load Skills]``
                                # block may carry a now-replaced skill. The
                                # once-per-instance gate suppresses a rebuild,
                                # so emit a ``RemoveMessage`` (graph_input)
                                # to drop the stale block this turn. A fresh
                                # filtered block re-materializes on the next
                                # first turn of a new instance.
                                _auto_load_sweep_agent_id = _aid or None
                            # ── Fix 4: SUPERSEDED LAST ─────────────────────
                            # Only after metadata is consistent do we stamp
                            # SUPERSEDED usage rows for the dropped IDs.
                            # If this raises, the orphan-sweep task picks
                            # the row up later — metadata is already
                            # correct so no double-stamp on retry.
                            if dropped:
                                metrics_service = getattr(
                                    self._manager, "_skill_metrics_service", None
                                )
                                if metrics_service is not None:
                                    try:
                                        metrics_service.finalize_superseded_skills(
                                            instance_id=_iid,
                                            agent_id=_aid,
                                            project_id=_pid or "",
                                            dropped_skill_ids=dropped,
                                        )
                                    except Exception as final_exc:
                                        logger.warning(
                                            f"Failed to finalize superseded "
                                            f"skills for {_iid[:8]}...: "
                                            f"{final_exc}"
                                        )
                            logger.info(
                                f"[MetaTag] REPLACE skill set for "
                                f"{_iid[:8]}...: old={len(existing)}, "
                                f"new={len(new_set)}, dropped={len(dropped)}"
                            )

                        await asyncio.to_thread(_finalize_and_replace)
                except Exception as e:
                    # Soft-fail — never block message processing on a
                    # meta-tag parse / lookup / persist error. The cleaned
                    # ``message`` text the top-of-function parse produced
                    # still flows through normally.
                    logger.warning(
                        f"Meta-tag skill loading failed for "
                        f"{instance_id[:8]}...: {e}"
                    )

        # ── Hybrid Context Injection (2026-07-29) — assemble persistent ──
        # Build the persistent context block ONCE per instance (on the
        # first turn) and prepend it to ``graph_input`` so LangGraph's
        # ``add_messages`` reducer checkpoints it with the user
        # message. From the next turn onward the persistent block
        # lives in ``state['messages']`` for free — no per-turn DB /
        # RAG rebuild.
        #
        # Skills (2026-07-29 refactor): moved from ephemeral to
        # PERSISTENT alongside project + shared-context. The skill
        # ``HumanMessage`` is now part of the persistent block too,
        # so it survives every turn via ``state['messages']`` and
        # is visible in the message history for debugging. The
        # pre-refactor comment that framed skills as "ephemeral and
        # continue to flow through the per-turn ContextSlot path" is
        # intentionally no longer accurate — the per-turn ContextSlot
        # path now serves only to BUILD the persistent skill message
        # on turns 2+ when a new skill triggers. ``agent_node`` no
        # longer re-injects skills into ``full_messages`` because
        # they enter via ``list(messages)`` from the checkpoint.
        #
        # ``project_already_injected`` (captured at the top of this
        # method) drives the orchestrator:
        #   * False (first turn) → orchestrator builds the full
        #     project + shared-context + skills triple; we use the
        #     persistent half here. Ephemeral is now always ``[]``
        #     (the orchestrator returns ``([...], [])``), so nothing
        #     needs to be cached on the manager for ``ContextSlot``.
        #   * True  (subsequent turns) → orchestrator skips the
        #     persistent project + shared-context builders (no DB /
        #     RAG I/O) but STILL runs the skills search; the freshly
        #     matched skill message lands in the persistent half and
        #     is prepended to ``graph_input`` for THIS turn so the
        #     reducer appends it to the checkpoint.
        #
        # Soft-fail: any ``assemble_context_messages`` exception is
        # logged + swallowed, falling back to the legacy layout
        # (no persistent block) so a transient DB / RAG error never
        # blocks message delivery.
        persistent_context_msgs: list[HumanMessage] = []
        if not is_retry:
            try:
                from .context_messages import assemble_context_messages

                # Read the cached skill result the skill-search
                # block just stored (B2 / B3 fix — reuse, do not
                # re-search). ``None`` falls through to the
                # orchestrator's internal ``_run_skill_search``
                # fallback for the rare case where the cache
                # was cleared between the search and this call.
                _cached_skill: tuple[str | None, list[str]] | None = None
                _skill_getter = getattr(
                    self._manager, "get_context_skill_result", None
                )
                if _skill_getter is not None:
                    _cached_skill = _skill_getter(instance_id)

                # Resolve ``project_id`` for the orchestrator.
                # ``agent_node`` reads it from instance metadata
                # each turn; mirror the same lookup here so the
                # persistent block on the first turn matches
                # what subsequent turns will see in
                # ``state['messages']``.
                _persistent_project_id: str | None = None
                try:
                    _proj_row = await asyncio.to_thread(
                        self._manager._instance_repository.get, instance_id
                    )
                    if _proj_row is not None and _proj_row.instance_metadata:
                        _persistent_project_id = (
                            _proj_row.instance_metadata.get("project_id")
                        )
                except Exception:  # pragma: no cover - defensive
                    _persistent_project_id = None

                # Resolve ``parent_id`` for tree-root resolution.
                # ``instance_meta`` may not be in scope here (it
                # is only assigned inside the ``if not
                # is_retry:`` block above); fall back to a fresh
                # ``None`` default so a stale reference cannot
                # leak through. The orchestrator treats
                # ``parent_id=None`` as "tree-root instance"
                # which is the correct default for our hybrid
                # path — child instances inherit the same
                # persistent context as their root via the
                # tree-root resolution inside the orchestrator.
                _persistent_parent_id: str | None = None

                _persistent_msgs, _ephemeral_msgs = await assemble_context_messages(
                    instance_id=instance_id,
                    user_query=message,
                    project_id=_persistent_project_id,
                    agent_meta=_messaging_agent_meta,
                    manager=self._manager,
                    instance_repository=self._manager._instance_repository,
                    parent_id=_persistent_parent_id,
                    skill_injection_result=_cached_skill,
                    project_already_injected=project_already_injected,
                    # A ``<meta>`` REPLACE recorded dropped skills →
                    # the checkpointed auto-load block may carry a
                    # now-replaced skill. Force a filtered rebuild
                    # (under the stable id) so only the surviving
                    # auto-load skills remain, instead of dropping
                    # them all (bare RemoveMessage) or leaking the
                    # replaced content.
                    auto_load_invalidated=bool(_auto_load_sweep_agent_id),
                )
                # 2026-07-29 refactor: ``_ephemeral_msgs`` is now
                # always ``[]`` in ``human_messages`` mode (skills
                # moved to the persistent half). The variable is
                # still unpacked for backward-compat with the call
                # signature — the orchestrator may return a populated
                # ephemeral half in some configurations, but only the
                # persistent half flows forward into
                # ``_build_graph_input``.

                if _persistent_msgs:
                    persistent_context_msgs = list(_persistent_msgs)
                    logger.info(
                        f"[Hybrid] Prepended {len(persistent_context_msgs)} "
                        f"persistent context message(s) (incl. skills "
                        f"since 2026-07-29 refactor) to graph_input for "
                        f"{instance_id[:8]}... (project_injected={project_already_injected})"
                    )

                # ── Auto-load REPLACE sweep (C3 leak fix) ────────────────
                # A ``<meta>`` REPLACE that dropped skills may have
                # invalidated the turn-1 checkpointed
                # ``[SYSTEM CONTEXT: Auto-Load Skills]`` block (the
                # once-per-instance gate suppresses a filtered rebuild).
                # Emit a ``RemoveMessage`` sentinel targeting the
                # stable block id so LangGraph's ``add_messages``
                # reducer drops the stale block from
                # ``state['messages']`` this turn — paired with the
                # filtered rebuild (``auto_load_invalidated``) so a
                # surviving set re-materializes under the same id.
                #
                # GATED on ``auto_load_block_active``: langgraph's
                # ``add_messages`` raises ``ValueError`` when a
                # ``RemoveMessage`` targets an id ABSENT from the
                # checkpoint. Agents without auto_load skills / no
                # project / no skill stack never build a block, so a
                # REPLACE of their BM25 skills there must NOT emit
                # the sweep (it would crash the message turn). On the
                # rebuild path a fresh same-id HumanMessage
                # supersedes the stale one regardless.
                _sweep_emitted = bool(
                    _auto_load_sweep_agent_id and auto_load_block_active
                )
                if _sweep_emitted:
                    from .context_messages import auto_load_skills_message_id
                    persistent_context_msgs.insert(
                        0,
                        RemoveMessage(
                            id=auto_load_skills_message_id(
                                instance_id, _auto_load_sweep_agent_id
                            )
                        ),
                    )
                    logger.info(
                        f"[Hybrid] Auto-load REPLACE sweep queued for "
                        f"{instance_id[:8]}... (agent="
                        f"{_auto_load_sweep_agent_id})"
                    )

                # ── Auto-load skills metadata tracking (dedup-merge) ───
                # Extract the auto-load skill IDs carried by the
                # ``[SYSTEM CONTEXT: Auto-Load Skills]`` HumanMessage
                # and dedup-merge them into ``last_injected_skill_ids``
                # via the shared ``_dedup_merge_skill_ids`` helper
                # (same path the BM25 block uses). This keeps the
                # orchestrator itself free of DB writes (read-path
                # safe) while still letting ``SkillMetricsService``
                # attribute usage records at task completion.
                #
                # Only on the first turn — the once-per-instance
                # contract means auto-load is already checkpointed
                # on subsequent turns, so this block is naturally
                # gated by ``not project_already_injected`` via the
                # outer ``if not is_retry`` boundary plus the
                # fact that ``_persistent_msgs`` only carries the
                # auto-load message on the first turn.
                _al_ids: list[str] = []
                _has_auto_load_block = False
                for _pm in persistent_context_msgs:
                    _ak = getattr(_pm, "additional_kwargs", None) or {}
                    if _ak.get("context_kind") != "auto_load_skills":
                        continue
                    _has_auto_load_block = True
                    # ``auto_load_skill_ids`` is always a list by
                    # construction (build_auto_load_skills_message),
                    # so no ``isinstance`` guard needed here.
                    _al_ids.extend(
                        str(x) for x in (_ak.get("auto_load_skill_ids") or [])
                        if x
                    )
                if _al_ids:
                    try:
                        await asyncio.to_thread(
                            _dedup_merge_skill_ids,
                            self._manager._instance_repository,
                            instance_id,
                            _al_ids,
                        )
                    except Exception as _al_exc:
                        logger.warning(
                            f"[Hybrid] Failed to persist auto-load "
                            f"skill IDs for {instance_id[:8]}...: "
                            f"{_al_exc}"
                        )
                # Maintain the ``auto_load_block_active`` flag so the
                # sweep on a future REPLACE turn knows whether a block
                # is checkpointed (gates the safe-to-emit RemoveMessage).
                # The flag mirrors ``state['messages']`` presence, NOT
                # ``persistent_context_msgs`` (which on a steady turn-2+
                # carry nothing because the block already lives in the
                # checkpoint). So only TRANSITION when this turn actually
                # changed block state:
                #   * fresh block built this turn → True (supersedes
                #     any swept stale one via the stable id).
                #   * sweep emitted with NO fresh rebuild (all skills
                #     replaced → empty) →False (stale removed, nothing
                #     replaces it).
                #   * neither → leave the flag untouched.
                if _has_auto_load_block:
                    _new_active = True
                elif _sweep_emitted:
                    _new_active = False
                else:
                    _new_active = auto_load_block_active  # unchanged
                if _new_active != auto_load_block_active:
                    try:
                        await asyncio.to_thread(
                            self._manager._instance_repository.set_metadata,
                            instance_id,
                            AUTO_LOAD_BLOCK_ACTIVE_KEY,
                            _new_active,
                        )
                    except Exception as _flag_set_exc:
                        logger.debug(
                            f"[Hybrid] Failed to update {AUTO_LOAD_BLOCK_ACTIVE_KEY} "
                            f"for {instance_id[:8]}...: {_flag_set_exc}"
                        )
            except Exception as _persist_exc:  # pragma: no cover - defensive
                logger.warning(
                    f"[Hybrid] Persistent context assembly failed for "
                    f"{instance_id[:8]}...: {type(_persist_exc).__name__}: "
                    f"{_persist_exc} — continuing without persistent prepending"
                )
                persistent_context_msgs = []

        # Build input - on retry with checkpoint, resume from None
        if not is_retry:
            await self._maybe_compact_context(instance_id, graph, config)

        if is_retry:
            has_ckpt = await self._has_checkpoint(instance_id)
            if has_ckpt:
                # Pass resume message as graph_input instead of aupdate_state.
                # aupdate_state(as_node="agent") clears checkpoint's next=() causing
                # astream(None) to return instantly without running the graph.
                # LangGraph's add_messages reducer appends the HumanMessage to existing
                # checkpoint messages, so the agent sees full history + new message.
                content = _build_message_content(message, images)
                if content and not silent:
                    # Resume on the existing checkpoint — no persistent
                    # prepending (the persistent block already lives in
                    # the checkpoint, and re-prepending would double-
                    # inject on the resume).
                    graph_input = _build_graph_input(
                        content, message_id,
                    )
                else:
                    # Pure checkpoint resume (silent mode or no content)
                    graph_input = None
            else:
                logger.warning(f"Retry for instance {instance_id[:8]}... but no checkpoint found, re-adding message")
                content = _build_message_content(message, images)
                graph_input = _build_graph_input(
                    content, message_id,
                )
        else:
            # First attempt - add message to conversation, with the
            # persistent context block (project + shared-context +
            # skills) prepended so LangGraph's ``add_messages`` reducer
            # checkpoints it once for all subsequent turns.
            content = _build_message_content(message, images)
            graph_input = _build_graph_input(
                content, message_id,
                persistent_context_msgs=persistent_context_msgs or None,
            )

        # Persistent context HumanMessages are graph inputs rather than normal
        # user turns, so they are not seen by the streaming loop's HumanMessage
        # skip below. Echo each newly prepended context message explicitly using
        # the same envelope as the regular user-message pre-emit. Stable
        # ids/content hashes make this safe when a message is encountered
        # again during retries or a repeated assembly path.
        context_messages_to_emit = list(persistent_context_msgs)
        if context_messages_to_emit:
            emitted_context_content = getattr(
                self._manager, "_emitted_message_content", None
            )
            if not isinstance(emitted_context_content, dict):
                emitted_context_content = {}
                self._manager._emitted_message_content = emitted_context_content
            for context_msg in context_messages_to_emit:
                context_serialized = serialize_message(context_msg)
                context_serialized["instance_id"] = instance_id
                context_id = context_serialized.get("message_id")
                context_hash = _compute_message_content_hash(context_serialized)
                # Include the content hash in the key as a fallback for legacy
                # skill messages whose generated id can differ on retry.
                context_key = (
                    f"{instance_id}:context:{context_id or context_hash}"
                )
                if (
                    emitted_context_content.get(context_key) == context_hash
                    or any(
                        key.startswith(f"{instance_id}:context:")
                        and value == context_hash
                        for key, value in emitted_context_content.items()
                    )
                ):
                    continue
                try:
                    await self._manager._live_hub.stream_message(
                        instance_id=instance_id,
                        message=context_serialized,
                        event_type="user_message",
                        checkpoint_id="user",
                    )
                except Exception as _context_emit_exc:  # pragma: no cover - defensive
                    logger.warning(
                        f"[Hybrid] Persistent context user_message SSE emit failed for "
                        f"{instance_id[:8]}...: {type(_context_emit_exc).__name__}: "
                        f"{_context_emit_exc}"
                    )
                else:
                    emitted_context_content[context_key] = context_hash

        # Build user message for pre-emit - use multimodal content if images present
        user_msg = HumanMessage(content=_build_message_content(message, images), id=message_id)
        
        user_serialized = serialize_message(user_msg)
        user_serialized["instance_id"] = instance_id
        await self._manager._live_hub.stream_message(
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
        # Per-invocation dedup of emitted tool_result events. Scoped here so the
        # set is reclaimed when this processing call returns — avoids the
        # per-process _original_timestamps map growing without bound.
        _emitted_tool_result_ids: set[str] = set()

        # C1 FIX (Phase 2 — User Language Preference): When language_check is
        # active, the agent node's final AIMessage would otherwise be dispatched
        # to the source BEFORE language_check runs and rewrites it, causing users
        # to see a wrong-language response followed by a corrected one. Defer
        # the final-message dispatch until the astream loop completes normally.
        # Retries naturally overwrite this buffer, so only the corrected (final)
        # message is ever sent to the external source.
        # W4 FIX: Read the flag from the compiled graph object (captures the
        # build-time config snapshot) rather than live config, which could be
        # mutated between graph build and message processing.
        language_check_active = bool(getattr(graph, 'language_check_active', False))
        _deferred_final_message: Any = None
        # C2 FIX: Track IDs of messages buffered for post-loop SSE re-emission.
        # The SSE emission loop iterates ``all_state_messages`` unconditionally
        # and would otherwise deliver the wrong-language AIMessage to the
        # frontend before language_check has had a chance to rewrite it. Using
        # the set keyed by msg.id lets us skip the *exact* buffered message
        # during the streaming loop and re-emit only the final (corrected)
        # version after ``astream`` completes.
        _deferred_msg_ids: set[str] = set()

        # Stream through graph execution
        # Register task for cancellation tracking INSIDE try block to prevent leaks
        # if CancelledError is raised during _maybe_compact_context
        current_task = asyncio.current_task()
        task_registered = False
        try:
            # Register current task for cancellation tracking
            if current_task:
                self._manager._graph_tasks[instance_id] = current_task
                task_registered = True
                logger.debug(f"Registered graph task for instance {instance_id[:8]}...")
            
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
                        if dispatch_source and self._manager.source_dispatcher:
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

                                        # W2: Normalize multimodal content to a string via the
                                        # shared helper so list/None/str handling stays in
                                        # lockstep with the language check node
                                        # (daemon.language_detection._normalize_content).
                                        content = _normalize_content(getattr(msg, 'content', None))

                                        if content and content.strip():
                                            # C1 FIX (Phase 2): If language_check is active AND this
                                            # is a final response (no tool_calls → will route through
                                            # language_check next), buffer it instead of dispatching
                                            # immediately. Retries overwrite the buffer so only the
                                            # corrected final message is sent. The msg_id is already
                                            # in _dispatched_msg_ids above, so state accumulation won't
                                            # re-trigger anything; the deferred dispatch after the
                                            # astream loop is the only external send.
                                            has_tool_calls = bool(getattr(msg, 'tool_calls', None))
                                            if language_check_active and not has_tool_calls:
                                                _deferred_final_message = msg
                                                # C2 FIX: Track the buffered message's id so
                                                # the SSE emission loop can skip the same
                                                # message — it will be re-emitted after the
                                                # astream loop completes (post language_check).
                                                if msg_id:
                                                    _deferred_msg_ids.add(msg_id)
                                                continue
                                            try:
                                                await self._manager.source_dispatcher.dispatch_message(
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

                        # Broadcast context usage against the latest accumulated
                        # state. _emit_context_usage dedupes so this is cheap
                        # when the token count is unchanged (e.g. during a long
                        # single-response stream).
                        await self._emit_context_usage(instance_id, all_state_messages)

                        # Build tool_outputs from ALL messages (including ToolMessages)
                        tool_outputs = {}
                        for m in all_state_messages:
                            if isinstance(m, ToolMessage):
                                tc_id, content_str = _stringify_tool_message_content(m)
                                if tc_id:
                                    tool_outputs[tc_id] = content_str
                        
                        # Build sequence ID for checkpoint_id
                        sequence_id = f"seq_{event_index}"
                        event_index += 1
                        
                        # Emit individual messages, preserving original created_at
                        for m in all_state_messages:
                            # Emit ToolMessage as a real-time tool_result event
                            # (also still baked into the next AIMessage's tool_calls
                            # for clients that don't yet handle tool_result).
                            if isinstance(m, ToolMessage):
                                tc_id, content_str = _stringify_tool_message_content(m)
                                if not tc_id:
                                    continue
                                # Dedup via a stable per-invocation key. ToolMessages
                                # lacking an `id` fall back to (tool_call_id, content)
                                # so the same tool call is never emitted twice across
                                # updates iterations of cumulative state.
                                original_id = getattr(m, "id", None)
                                if original_id:
                                    dedup_key = f"id:{original_id}"
                                else:
                                    dedup_key = f"tc:{tc_id}:{content_str}"
                                if dedup_key in _emitted_tool_result_ids:
                                    continue
                                _emitted_tool_result_ids.add(dedup_key)
                                await self._manager._live_hub.stream_tool_result(
                                    instance_id=instance_id,
                                    tool_call_id=tc_id,
                                    content=content_str,
                                    message_id=original_id or dedup_key,
                                )
                                continue
                            # Skip HumanMessages — already emitted before graph started
                            if hasattr(m, 'type') and m.type == 'human':
                                continue
                            
                            msg_id = getattr(m, 'id', None)
                            # C2 FIX: Skip messages buffered for deferred SSE emission.
                            # When language_check is active, the buffered AI message
                            # may be rewritten/retried during the astream loop. The
                            # post-loop block re-emits the *final* version via SSE,
                            # so emitting here would deliver a wrong-language message
                            # to the frontend first. Fall back to ``message_id`` for
                            # consistency with the dispatcher's id resolution.
                            msg_id_check = msg_id or getattr(m, 'message_id', None)
                            if msg_id_check and msg_id_check in _deferred_msg_ids:
                                continue
                            msg_serialized = serialize_message(m, tool_outputs)
                            msg_serialized["instance_id"] = instance_id
                            
                            # Preserve original created_at from first emission
                            ts_key = f"{instance_id}:{msg_id}" if msg_id else None
                            if ts_key and ts_key in self._manager._original_timestamps:
                                msg_serialized["created_at"] = self._manager._original_timestamps[ts_key]
                            elif ts_key:
                                self._manager._original_timestamps[ts_key] = msg_serialized["created_at"]
                            
                            # Store content hash for deduplication (skip if content unchanged)
                            if ts_key:
                                content_hash = _compute_message_content_hash(msg_serialized)
                                self._manager._emitted_message_content[ts_key] = content_hash
                            
                            # Emit individually
                            event_type = _get_message_event_type(msg_serialized)
                            await self._manager._live_hub.stream_message(
                                instance_id=instance_id,
                                message=msg_serialized,
                                event_type=event_type,
                                checkpoint_id=sequence_id,
                            )
                        
                        # Track final content and last AI message from streaming
                        for msg in reversed(all_state_messages):
                            if hasattr(msg, 'type') and msg.type == 'ai':
                                if hasattr(msg, 'content'):
                                    # Normalize multimodal content (str | list | None) via
                                    # the shared _normalize_content helper for parity with
                                    # the language check node and the progressive
                                    # dispatch loop above.
                                    final_content = _normalize_content(msg.content)
                                last_ai_message = msg
                                break

        except asyncio.CancelledError:
            # Graph was cancelled by pause_instance_cascade
            # Re-raise so caller (MessageJobHandler/ProcessMessageProcessor) can
            # distinguish pause-cancel from normal completion and leave job PROCESSING
            logger.info(f"Graph execution cancelled for instance {instance_id[:8]}... (message_id={message_id[:8]}...)")
            raise

        except Exception as e:
            logger.error(f"Streaming failed for message {message_id}: {e}")
            await self._manager._live_hub.stream_error(
                instance_id=instance_id,
                error={"error": str(e), "stage": "streaming", "message_id": message_id},
            )
            raise

        finally:
            # C2 fix — deferred question pause (Solution A), second pass.
            #
            # ``question_pause_node`` ran inside this graph task and set a
            # marker rather than calling ``pause_instance_cascade`` directly
            # (to avoid self-cancel of this very task). The graph task is now
            # popped from ``_graph_tasks`` so we are safely OUTSIDE the
            # graph-task context — calling the cascade here will not
            # self-cancel; the DB transaction completes normally.
            #
            # HOISTED out of the ``if existing is current_task`` guard below:
            # if an external ``pause_instance_cascade`` already pre-popped
            # ``_graph_tasks[instance_id]`` (e.g. user-click-stop racing the
            # graph completion), the identity check fails and the marker
            # would otherwise leak — causing a spurious pause on the next
            # message. ``pop_deferred_question_pause`` is idempotent
            # (``set.discard``), so it's safe to call unconditionally.
            #
            # C1 FIX (marker lifetime): the marker is PEEKED with
            # ``has_deferred_question_pause`` BEFORE the cascade and POPPED
            # with ``pop_deferred_question_pause`` in the inner ``finally``
            # block AFTER ``pause_instance_cascade`` completes. The old
            # "pop-before-cascade" ordering left the marker empty during
            # the cascade's DB-commit window (DB still RUNNING) so
            # source-side Task guards saw ``marker=False, db=RUNNING`` and
            # CREATED a spurious Task. Extending the marker lifetime past
            # the cascade's DB commit closes that race. Safe because:
            #   * the marker is in-memory only (no DB write) — moving the
            #     pop cannot introduce a DB torn state;
            #   * the cascade is wrapped in ``asyncio.shield`` so the DB
            #     write completes regardless of outer cancellation;
            #   * ``pause_instance_cascade`` does NOT touch
            #     ``_deferred_question_pause`` (confirmed by grep — no
            #     reference in ``instance_lifecycle.py``);
            #   * ``pop_deferred_question_pause`` is idempotent.
            #
            # SHIELDED against double-cancel: a second ``task.cancel()``
            # arriving during the ``await`` would raise ``CancelledError``
            # (a ``BaseException`` in 3.8+, NOT caught by ``except Exception``).
            # ``asyncio.shield`` protects the DB write so a transient cancel
            # during the pause cascade does not corrupt instance state.
            #
            # This runs on every exit path (normal completion,
            # CancelledError, exception) because the cascade peek/pop is
            # unconditional — a no-op when no marker was set.
            #
            # Wrapped in try/except so a transient cascade failure does not
            # crash the message-processing call. The question pack SSE has
            # already fired from the tool, so the user can still answer; the
            # instance will just remain in whatever status the graph
            # completed in. Re-pausing an already-PAUSED instance is a
            # no-op (``pause_instance_cascade`` filters out PAUSED nodes
            # at line 1966), so a residual marker on top of an external
            # pause is harmless.
            if self._manager.has_deferred_question_pause(instance_id):
                try:
                    await asyncio.shield(
                        self._manager.pause_instance_cascade(instance_id)
                    )
                except Exception as pause_err:
                    logger.warning(
                        f"[process_message] deferred question pause "
                        f"failed for {instance_id[:8]}...: "
                        f"{type(pause_err).__name__}: {pause_err}"
                    )
                finally:
                    # Pop AFTER the cascade completes so the marker
                    # covers the full cascade-execution window (DB
                    # commit to PAUSED). Closes C1.
                    self._manager.pop_deferred_question_pause(instance_id)

            # Always unregister the task, but only if we're still the registered task
            # (handles race condition where new execution starts before our finally runs)
            if task_registered and current_task:
                existing = self._manager._graph_tasks.get(instance_id)
                if existing is current_task:
                    self._manager._graph_tasks.pop(instance_id, None)
                    self._manager.release_context_usage_cache(instance_id)
                    logger.debug(f"Unregistered graph task for instance {instance_id[:8]}...")

        # C1 FIX (Phase 2): Dispatch the deferred final message AFTER the astream
        # loop completes normally. This code only runs on successful completion —
        # asyncio.CancelledError is re-raised above and skips this block, so a
        # cancelled response is never sent to the external source.
        if _deferred_final_message is not None:
            # Normalize multimodal content (str | list | None) via the shared
            # _normalize_content helper for parity with the language check
            # node and the progressive dispatch loop above.
            deferred_content = _normalize_content(getattr(_deferred_final_message, 'content', ''))
            if (
                deferred_content
                and deferred_content.strip()
                and dispatch_source
                and self._manager.source_dispatcher
            ):
                try:
                    await self._manager.source_dispatcher.dispatch_message(
                        source=dispatch_source,
                        content=deferred_content,
                    )
                except Exception as e:
                    logger.warning(
                        f"Deferred dispatch failed for message {message_id[:8]}...: {e}"
                    )

            # C2 FIX: Also re-emit the deferred message via SSE so the frontend
            # sees the *final* (post-language_check) version. The in-loop SSE
            # emission skipped this message via ``_deferred_msg_ids``; we now
            # flush it here using the same serialization pattern as the loop.
            try:
                # Reconstruct tool_outputs from the final accumulated state,
                # mirroring the in-loop logic so any inline tool_call output
                # matches what the frontend would have seen inline.
                deferred_tool_outputs: dict[str, str] = {}
                for mm in all_state_messages:
                    if isinstance(mm, ToolMessage):
                        tc_id, content_str = _stringify_tool_message_content(mm)
                        if tc_id:
                            deferred_tool_outputs[tc_id] = content_str

                deferred_serialized = serialize_message(
                    _deferred_final_message,
                    deferred_tool_outputs,
                )
                deferred_serialized["instance_id"] = instance_id

                # Preserve original created_at from first emission, same as loop.
                deferred_msg_id = getattr(_deferred_final_message, 'id', None)
                deferred_ts_key = (
                    f"{instance_id}:{deferred_msg_id}" if deferred_msg_id else None
                )
                if deferred_ts_key and deferred_ts_key in self._manager._original_timestamps:
                    deferred_serialized["created_at"] = (
                        self._manager._original_timestamps[deferred_ts_key]
                    )
                elif deferred_ts_key:
                    self._manager._original_timestamps[deferred_ts_key] = (
                        deferred_serialized["created_at"]
                    )

                deferred_event_type = _get_message_event_type(deferred_serialized)
                await self._manager._live_hub.stream_message(
                    instance_id=instance_id,
                    message=deferred_serialized,
                    event_type=deferred_event_type,
                    checkpoint_id=f"seq_{event_index}",
                )
            except Exception as e:
                logger.warning(
                    f"Deferred SSE dispatch failed for message {message_id[:8]}...: {e}"
                )

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
        await self._manager.get_instance(instance_id)  # raises KeyError if not found
        
        if self._checkpointer:
            # Pass the manager so get_instance_messages can inject the
            # synthetic system prompt (which is NOT persisted to the
            # checkpoint but is needed by the frontend's
            # "View system message" toggle).
            return await get_instance_messages(
                self._checkpointer, instance_id, manager=self._manager
            )
        return []

    async def get_queue_stats(self, instance_id: str) -> dict:
        """Get queue statistics for an instance.

        Returns a dict with pending_count, processing_count,
        and oldest_message_age_seconds attributes.
        """
        stats = await asyncio.to_thread(self._queue_repository.get_stats, instance_id)
        return {
            "pending_count": stats["pending_count"],
            "processing_count": stats["processing_count"],
            "oldest_message_age_seconds": stats["oldest_message_age_seconds"]
        }

