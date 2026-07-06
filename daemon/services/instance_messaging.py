"""Instance messaging service for sending and processing messages."""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NamedTuple

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, ToolMessage
from sqlmodel import Session

from ..cancellation import CancellationToken
from ..compaction import ContextCompactor, CompactionContext, get_model_context_limit
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
        """
        try:
            messages = await self.get_messages(instance_id)
        except Exception as e:
            logger.debug(f"emit_context_usage_for_instance: get_messages failed for {instance_id[:8]}...: {e}")
            return

        # get_messages returns dicts; convert to lightweight LangChain messages
        # so estimate_messages_tokens can read .content / .tool_calls / .name.
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )
        _cls_for_role = {
            "user": HumanMessage,
            "assistant": AIMessage,
            "system": SystemMessage,
            "tool": ToolMessage,
        }
        adapted = []
        for raw in messages or []:
            cls = _cls_for_role.get(raw.get("role", "user"), HumanMessage)
            msg = cls(content=raw.get("content", "") or "")
            if raw.get("id"):
                msg.id = raw["id"]
            if raw.get("tool_calls"):
                msg.tool_calls = raw["tool_calls"]
            if raw.get("name"):
                msg.name = raw["name"]
            adapted.append(msg)

        await self._emit_context_usage(instance_id, adapted, force=True)

    def _fetch_critical_notes_safe(self, project_id: str) -> list[dict]:
        """Fetch critical notes for a project, returning empty list on failure."""
        try:
            return [n.to_dict() for n in self._project_repository.list_critical_notes(project_id)]
        except Exception as e:
            logger.warning(f"Failed to fetch critical notes for project {project_id}: {e}")
            return []

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
        """Fire-and-forget title generation if conditions are met."""
        if should_trigger:
            MainLoopBridge.run_async_no_wait(
                self._manager._generate_and_broadcast_title(instance_id, message)
            )
            logger.debug(f"Title generation triggered for first message to instance {instance_id[:8]}...")

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

        # Invoke with message
        config = {
            "configurable": {"thread_id": instance_id},
            "recursion_limit": self._config.limits.graph_recursion_limit,
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
        work_id: str | None = None,
    ) -> _PreparedEnqueueContext:
        """Shared prelude for ``enqueue_message``.

        Writes the atomic MessageQueue + Task + Event trio that every
        message enqueue needs:

        - Reject messages during shutdown.
        - Resolve ``msg_type`` from the ``source`` prefix and mint a UUID.
        - Insert the ``MessageQueue`` row.
        - Insert the ``Task`` row in the same transaction (so the two
          either both commit or both roll back together — this is the
          D13 structural fix that eliminated the dual-record coupling).
        - Auto-resume ``IDLE`` / ``WAITING_CHILDREN`` / ``COMPLETED`` instances
          to ``RUNNING`` and bump ``last_activity_at`` / ``version``.
        - Append a ``MESSAGE_RECEIVED`` event for event-sourced features.
        - Commit the session.

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

        Returns:
            ``_PreparedEnqueueContext`` carrying the values callers need to
            proceed with dispatch (SSE emit, title generation, WorkerPool
            notify).
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
        # Task row is always created in the same transaction as the
        # MessageQueue row. ``task_id`` is its primary key (int | None) —
        # None only if the task insert failed for an unrecoverable reason
        # (callers treat None as "no resolvable work_id available"). The
        # HTTP route discards ``job_id``; the ``job_continue`` tool uses it
        # as ``new_job_id`` (resolution goes through
        # ``work_resolver.resolve_work`` against ``task`` and
        # ``job_queue_items`` — see ``enqueue_message``).
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

            # 2. Insert the Task row in the same transaction as the
            #    MessageQueue row. The structural D13 fix that eliminates
            #    the dual-record coupling — messages no longer create a
            #    JobItem at all; the Task row IS the dispatch primitive.
            #
            #    ``is_deferred`` (Phase 3 Part B1, 2026-06-27) is
            #    stamped at creation time so the defer-queue idle gate
            #    can recognise the row without a follow-up UPDATE.
            #    Default False matches every pre-existing caller; the
            #    orchestrator opts in via ``enqueue_message``.
            task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
                is_deferred=is_deferred,
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
            # ``task.work_id`` was either inherited from the caller
            # (``enqueue_message_job``'s shared UUID, satisfying the
            # linkage contract with JobItem.job_id) or minted above by
            # ``default_factory`` (legacy ``enqueue_message`` path).
            # No re-capture needed — the local ``work_id`` variable
            # already holds the correct value either way.

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
            # can surface it as ``AsyncMessageResult.job_id``.
            # ``task.id`` is populated by the autoincrement; refresh()
            # re-reads the row from the DB to pick it up.
            try:
                session.refresh(task)
                task_id = task.id
            except Exception as e:
                # Should not happen — the insert succeeded (we're past
                # commit). Log and continue with None so callers degrade
                # gracefully (HTTP route doesn't read job_id; job_continue
                # would get None and surface a clear error).
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
            work_id=work_id,
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
    ) -> "AsyncMessageResult":
        """POC variant of :meth:`enqueue_message` that also creates a JobItem.

        Mirrors ``enqueue_message`` end-to-end, then additionally creates a
        ``JobItem(job_type='message', job_id=task.work_id, admission_state='queued')``
        in the same logical dispatch window. The JobItem is the
        informational mirror of the Task's running state — the Task is
        still the authoritative serialization gate.

        Why a sibling method instead of a flag on ``enqueue_message``:

        * The existing D13 flow (``enqueue_message`` only writes Task +
          MessageQueue) is frozen. A parallel method keeps the diff
          additive so a flag flip can A/B the two without one path
          silently regressing.
        * ``JobQueueService.enqueue_job`` rejects ``job_type='message'``
          with ``ValueError`` (D13 defense-in-depth). This method calls
          ``JobRepository.create`` directly — the lowest-level insert
          that bypasses the service-layer guard.

        JobItem ↔ Task linkage:

        * ``job_id = task.work_id`` — the same UUID4 minted by the Task
          model's ``default_factory`` in the prelude. The
          ``stamp_message_id`` correlation key goes onto the JobItem's
          ``metadata.message_id`` so the cross-system guard can resolve
          a claimed MESSAGE JobItem back to the originating message.
        * ``instance_id`` matches the Task row. The post-claim
          activation in ``worker_pool`` flips the JobItem queued→active
          as an informational mirror; the Task's running state remains
          the authoritative gate.

        Failure handling:

        * If the JobRepository is unavailable (test fixtures), the
          method logs and skips JobItem creation — ``enqueue_message``
          semantics are preserved so callers don't see a regression.
        * If ``JobRepository.create`` or ``stamp_message_id`` raises,
          the exception is logged at warning level and the Task row
          remains the sole dispatch primitive. The message still gets
          processed (Task IS the dispatch primitive); the JobItem is a
          derived mirror, not a gate.

        Args:
            instance_id: Target instance ID.
            message: User content.
            source: Source tag (e.g. ``"api"``, ``"telegram:user:1"``).
            priority: 0=system, 1=user (matches ``enqueue_message``).
            images: Optional base64 images for vision messages.
            metadata: Optional metadata dict.
            is_deferred: Forwarded to ``enqueue_message`` — stamps
                ``Task.is_deferred=True`` so the worker pool's idle gate
                holds the task until every non-defer queue is empty.

        Returns:
            ``AsyncMessageResult`` with ``message_id``, ``instance_id``,
            ``status="queued"``, and ``job_id`` populated as the shared
            UUID4 (Task.work_id == JobItem.job_id). HTTP callers discard
            ``job_id``; tooling that needs the cross-system handle
            receives a real one.
        """
        from ..registry import get_registry
        from ..repositories.job_queue.models import AdmissionState

        # Step 1: write MessageQueue + Task via the frozen prelude.
        # This mints ``job_id = task.work_id`` (UUID4) inside the same
        # transaction as the MessageQueue row.
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
            work_id=job_id,
        )

        # Step 2: emit status change if IDLE/WAITING_CHILDREN → RUNNING.
        # Mirrors ``enqueue_message``'s post-prelude SSE emit so the
        # frontend sees identical behavior on the wire.
        if ctx.status_changed_to_running:
            await self._manager._live_hub.stream_status_change(
                instance_id, InstanceStatus.RUNNING.value, agent_id=ctx.instance_agent_id
            )

        # Step 3: fire-and-forget title generation for the first message.
        self._maybe_trigger_title_generation(
            instance_id, message, ctx.is_idle_to_running
        )

        # Step 4: create the JobItem mirror. Best-effort — the Task is
        # the authoritative dispatch primitive, so a JobItem creation
        # failure must not break message enqueue. When the JobRepository
        # is unavailable (test fixtures, pre-wiring bootstrap) we
        # quietly skip the mirror creation.
        job_repo = self._job_repository
        if job_repo is None:
            logger.debug(
                "enqueue_message_job: JobRepository unavailable for instance "
                f"{instance_id[:8]}... — skipping JobItem mirror creation. "
                "Task row remains the sole dispatch primitive."
            )
        else:
            try:
                # Resolve agent_dir from the registry. Same lookup
                # JobQueueService.enqueue_job uses; we can't share the
                # helper without dragging in the project_id resolution
                # path that the message flow does not have.
                agent_id_for_job = ctx.instance_agent_id or "default"
                registry = get_registry()
                agent_meta = registry.get_resolved(agent_id_for_job)
                if agent_meta is None:
                    # Fall back to the instance's stored agent_id if
                    # registry lookup fails (e.g. unregistered agent).
                    # The JobItem still gets a usable row; downstream
                    # consumers can re-resolve.
                    agent_dir_value = ""
                    resolved_agent_id = agent_id_for_job
                else:
                    agent_dir_value = str(agent_meta.path)
                    resolved_agent_id = (
                        registry.resolve_pure_id(agent_id_for_job)
                        or agent_id_for_job
                    )

                # Bypass JobQueueService.enqueue_job (D13 rejects
                # job_type='message') and call the repository's low-level
                # create() directly. The repository already sets
                # admission_state=QUEUED by default — no need to override.
                #
                # ``job_id=job_id`` is the linkage handle: the same
                # UUID4 we passed to ``_prepare_enqueued_message(work_id=...)``
                # is forwarded to ``JobItem.job_id`` so the two rows
                # share one handle (the linkage contract). Passing it
                # explicitly suppresses ``JobItem``'s ``default_factory``
                # for this row, which is what makes the equality hold.
                await asyncio.to_thread(
                    job_repo.create,
                    agent_id=resolved_agent_id,
                    agent_dir=agent_dir_value,
                    message=message,
                    source=source,
                    priority=priority,
                    job_metadata={},
                    queue_id=None,
                    idempotency_key=None,
                    job_type="message",
                    instance_id=instance_id,
                    job_id=job_id,
                    max_retries=0,  # message Jobs do not retry — observer finalizes them
                )

                # Eagerly flip the mirror queued→active BEFORE stamping
                # the message_id. The post-claim worker hook in
                # ``_activate_message_jobitem_async`` would otherwise
                # run AFTER ``claim_pending_task``, and Phase-2
                # second messages were getting blocked by the cross-
                # system guard carving out a still-queued JobItem as
                # a "blocker" before the worker could flip the state.
                # Eager activation aligns with the POC contract:
                # "Task IS the dispatch primitive; JobItem is a
                # derived mirror" — the mirror should be active as
                # soon as it is created. ``atomic_transition`` is
                # race-safe (single-statement UPDATE with
                # ``WHERE admission_state='queued'`` guard) and is
                # idempotent with the worker hook, which will see
                # ``InvalidTransitionError`` when the row is no
                # longer in ``queued`` and swallow it at debug.
                try:
                    await asyncio.to_thread(
                        job_repo.atomic_transition,
                        job_id,
                        AdmissionState.QUEUED.value,
                        AdmissionState.ACTIVE.value,
                    )
                except Exception as activate_exc:
                    # Mirror activation is best-effort. The Task row
                    # is the authoritative dispatch primitive; a
                    # still-queued JobItem only delays the worker's
                    # post-claim activation (which is itself a no-op
                    # for the dispatch path). Log and continue.
                    logger.debug(
                        f"enqueue_message_job: eager JobItem "
                        f"activation failed for "
                        f"job_id={job_id[:8]}...: "
                        f"{type(activate_exc).__name__}: "
                        f"{activate_exc}"
                    )

                # Cross-system guard correlation — the observer finalize
                # path and the worker claim path both resolve MESSAGE
                # JobItems back to the originating message_id via
                # ``job_queue_items.metadata->>'message_id'``. Without
                # this stamp, the correlation returns NULL and the
                # cross-system guard cannot match.
                await asyncio.to_thread(
                    job_repo.stamp_message_id,
                    job_id,
                    ctx.message_id,
                )

                logger.debug(
                    f"enqueue_message_job: created JobItem mirror "
                    f"job_id={job_id[:8]}... for instance {instance_id[:8]}... "
                    f"message_id={ctx.message_id[:8]}..."
                )
            except Exception as e:
                # Mirror creation is best-effort. The Task row is the
                # authoritative dispatch primitive; a missing JobItem
                # just means the WorkResolver facade sees fewer rows,
                # not that message processing breaks.
                logger.warning(
                    f"enqueue_message_job: JobItem mirror creation failed "
                    f"for instance {instance_id[:8]}... message "
                    f"{ctx.message_id[:8]}...: {type(e).__name__}: {e}. "
                    "Task row remains the sole dispatch primitive."
                )

        # Step 5: notify the WorkerPool (Task row already written by
        # the prelude). Same dispatch path as ``enqueue_message`` —
        # the JobItem mirror is informational only and does not change
        # which primitive drives worker claim.
        if self._manager._worker_pool is not None:
            self._manager._worker_pool.notify_work()

        logger.debug(
            f"Enqueued message {ctx.message_id} (job-mirror) for instance "
            f"{instance_id} job_id={job_id[:8]}..."
        )

        return AsyncMessageResult(
            message_id=ctx.message_id,
            instance_id=instance_id,
            status="queued",
            job_id=job_id,
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
        
        # Project context injection for first message only
        if not is_retry:
            is_completion_report = (
                message_source is not None and (
                    message_source.startswith("internal_report:") or
                    message_source.startswith("internal_error_report:") or
                    message_source.startswith("internal_agent:job_event:")
                )
            )
            
            if is_completion_report:
                # Skip project injection for completion/error reports
                pass
            else:
                # Check if project was already injected (using metadata flag).
                # Wrap the sync DB read in ``asyncio.to_thread`` (see deadlock
                # analysis in experience docs).
                instance_meta = await asyncio.to_thread(
                    self._manager._instance_repository.get, instance_id
                )
                project_already_injected = (
                    instance_meta and 
                    instance_meta.instance_metadata and 
                    instance_meta.instance_metadata.get("project_injected")
                )
                
                if not project_already_injected:
                    # First injection → attempt project injection
                    existing_project_id = None
                    if instance_meta and instance_meta.instance_metadata:
                        existing_project_id = instance_meta.instance_metadata.get("project_id")
                    
                    injection_succeeded = False
                    
                    if existing_project_id:
                        # project_id exists (inherited from parent) → inject context using stored project_id.
                        # Wrap the sync project_repo DB read in ``asyncio.to_thread``.
                        matched_project = await asyncio.to_thread(
                            self._project_repository.get, existing_project_id
                        )
                        if matched_project:
                            from ..manager import format_project_context
                            # ``_fetch_critical_notes_safe`` is itself a sync
                            # helper that does DB — wrap the call in a thread.
                            critical_notes = await asyncio.to_thread(
                                self._fetch_critical_notes_safe,
                                matched_project.project_id,
                            )
                            project_context = format_project_context(matched_project, store=self._manager.project_store, critical_notes=critical_notes)
                            message = project_context + message
                            injection_succeeded = True
                            logger.info(f"Project context injection: using stored project_id '{existing_project_id}' for instance {instance_id[:8]}...")
                    else:
                        # No project_id yet → extract keywords and try to match
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
                                
                                # Fetch critical notes from repository.
                                # ``_fetch_critical_notes_safe`` is sync — wrap.
                                critical_notes = await asyncio.to_thread(
                                    self._fetch_critical_notes_safe,
                                    matched_project.project_id,
                                )
                                
                                # Prepend project context to message
                                from ..manager import format_project_context
                                project_context = format_project_context(matched_project, store=self._manager.project_store, critical_notes=critical_notes)
                                message = project_context + message
                                injection_succeeded = True
                                
                                # Update instance metadata with project_id.
                                # Sync DB write — wrap in ``asyncio.to_thread``.
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
                    graph_input = {"messages": [HumanMessage(content=content, id=message_id)]}
                else:
                    # Pure checkpoint resume (silent mode or no content)
                    graph_input = None
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
                                    content = msg.content
                                    # Handle list content (e.g., [{"type": "text", "text": "..."}])
                                    if isinstance(content, list):
                                        text_parts = [
                                            b.get("text", "")
                                            for b in content
                                            if isinstance(b, dict) and b.get("text")
                                        ]
                                        final_content = " ".join(text_parts)
                                    else:
                                        final_content = content or ""
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
            # Always unregister the task, but only if we're still the registered task
            # (handles race condition where new execution starts before our finally runs)
            if task_registered and current_task:
                existing = self._manager._graph_tasks.get(instance_id)
                if existing is current_task:
                    self._manager._graph_tasks.pop(instance_id, None)
                    self._manager.release_context_usage_cache(instance_id)
                    logger.debug(f"Unregistered graph task for instance {instance_id[:8]}...")

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
            return await get_instance_messages(self._checkpointer, instance_id)
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

