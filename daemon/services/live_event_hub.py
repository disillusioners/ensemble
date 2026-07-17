"""Live-only SSE event hub - no buffering.

Events are only streamed to active SSE connections. If no client is listening,
events are dropped silently (fire-and-forget).
"""
import asyncio
import logging
from typing import Any

from daemon.repositories.instance.repository import KB_AGENT_IDS

logger = logging.getLogger(__name__)


class LiveEventHub:
    """Hub for live-only SSE streaming without buffering.
    
    Each SSE connection registers its own asyncio.Queue. When events are
    broadcasted, they're sent directly to all registered queues (if any).
    If no queues are registered, events are dropped.
    
    This replaces the old EventBus which buffered events regardless of
    client connection state.
    """
    
    def __init__(self, max_queue_size: int = 50) -> None:
        """Initialize LiveEventHub.
        
        Args:
            max_queue_size: Max size per connection queue (backpressure).
        """
        self._max_queue_size = max_queue_size
        
        # Per-instance connection registry: instance_id -> set of Queues
        self._connections: dict[str, set[asyncio.Queue]] = {}
        
        # Lock for thread-safe connection management
        self._lock = asyncio.Lock()
    
    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------
    
    async def add_connection(self, instance_id: str, queue: asyncio.Queue) -> None:
        """Register an SSE connection for an instance.
        
        Args:
            instance_id: The instance to stream events for.
            queue: The connection's asyncio.Queue for receiving events.
        """
        async with self._lock:
            if instance_id not in self._connections:
                self._connections[instance_id] = set()
            self._connections[instance_id].add(queue)
            logger.debug(f"Connection added for {instance_id}, total: {len(self._connections[instance_id])}")
    
    async def remove_connection(self, instance_id: str, queue: asyncio.Queue) -> None:
        """Unregister an SSE connection.
        
        Args:
            instance_id: The instance ID.
            queue: The connection's queue to remove.
        """
        async with self._lock:
            if instance_id in self._connections:
                self._connections[instance_id].discard(queue)
                if not self._connections[instance_id]:
                    del self._connections[instance_id]
                logger.debug(f"Connection removed for {instance_id}")
    
    async def get_connection_count(self, instance_id: str) -> int:
        """Get number of active connections for an instance.
        
        Args:
            instance_id: The instance ID.
            
        Returns:
            Number of active SSE connections.
        """
        async with self._lock:
            return len(self._connections.get(instance_id, set()))
    
    # -------------------------------------------------------------------------
    # Event Streaming
    # -------------------------------------------------------------------------
    
    async def stream_checkpoint(
        self,
        instance_id: str,
        messages: list[dict],
        checkpoint_id: str,
        tool_outputs: dict | None = None,
    ) -> None:
        """Stream checkpoint event to all active connections.
        
        Args:
            instance_id: The instance this checkpoint belongs to.
            messages: Pre-serialized list of message dicts.
            checkpoint_id: Checkpoint ID from LangGraph state.
            tool_outputs: Optional tool outputs map.
        """
        if not messages:
            return
        
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "checkpoint",
            "event_id": checkpoint_id,
            "messages": messages,
            "checkpoint_id": checkpoint_id,
        }
        if tool_outputs:
            event["tool_outputs"] = tool_outputs
        
        await self._stream_to_connections(instance_id, event)
    
    async def stream_tool_result(
        self,
        instance_id: str,
        tool_call_id: str,
        content: str,
        message_id: str,
    ) -> None:
        """Stream a real-time tool result event to all active connections.

        Emitted in real time as the graph's `tools` node finishes a tool call,
        independent of the next assistant message. The payload is intentionally
        minimal so the frontend can patch the matching tool_calls[i].output
        in place.

        Args:
            instance_id: The instance the tool result belongs to.
            tool_call_id: The id of the tool call this result answers.
            content: The tool's output content (stringified).
            message_id: The ToolMessage's id, used as event_id for dedup.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "tool_result",
            "event_id": message_id,
            "message": {
                "message_id": message_id,
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            },
        }
        await self._stream_to_connections(instance_id, event)

    async def stream_message(
        self,
        instance_id: str,
        message: dict,
        event_type: str = "message",
        checkpoint_id: str | None = None,
    ) -> None:
        """Stream message event to all active connections.
        
        Args:
            instance_id: The instance this message belongs to.
            message: Pre-serialized message dict.
            event_type: Type of message event.
            checkpoint_id: Optional checkpoint ID.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
            "event_id": message.get("message_id", ""),
            "message": message,
            "checkpoint_id": checkpoint_id,
        }
        
        await self._stream_to_connections(instance_id, event)
    
    async def _stream_to_connections(self, instance_id: str, event: dict[str, Any]) -> None:
        """Stream event to all active connections for an instance.
        
        If no connections exist, the event is silently dropped.
        
        Args:
            instance_id: The instance ID.
            event: The event dict to stream.
        """
        async with self._lock:
            connections = list(self._connections.get(instance_id, set()))
            dead_queues = []
            
            for queue in connections:
                try:
                    queue.put_nowait(event)
                except (asyncio.QueueFull, asyncio.QueueShutDown):
                    dead_queues.append(queue)
            
            # Clean up dead connections (queues that are full = slow consumer)
            for q in dead_queues:
                self._connections.get(instance_id, set()).discard(q)
    
    # -------------------------------------------------------------------------
    # Lifecycle Events (still need DB persistence + notification)
    # -------------------------------------------------------------------------
    
    async def stream_error(
        self,
        instance_id: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Stream error event to active connections.
        
        Args:
            instance_id: The instance ID.
            error: Error data dict.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "error",
            "error": error,
        }
        await self._stream_to_connections(instance_id, event)
    
    async def stream_status_change(
        self,
        instance_id: str,
        status: str,
        agent_id: str | None = None,
        job_status: str | None = None,
    ) -> None:
        """Stream status change event to all active connections.

        Args:
            instance_id: The instance ID.
            status: The new status value.
            agent_id: The agent ID (optional, for filtering KB instances on frontend).
            job_status: Optional companion job status (e.g. ``"paused"``
                after a cascade pause — see Phase 2 pause/resume
                redesign, 2026-06-25). When present, the SSE payload
                carries both ``status`` (instance) and ``job_status``
                so the frontend can render them side-by-side without
                waiting for a separate job-status event.
        """
        # Skip KB agents to avoid polluting SSE with internal agent events
        if agent_id is not None and agent_id in KB_AGENT_IDS:
            return

        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "status_change",
            "status": status,
        }
        if agent_id is not None:
            event["agent_id"] = agent_id
        if job_status is not None:
            event["job_status"] = job_status
        await self._stream_to_connections(instance_id, event)

    async def stream_context_usage(
        self,
        instance_id: str,
        tokens: int,
        context_window: int,
        model_name: str,
    ) -> None:
        """Stream current context-window usage to all active connections.

        The frontend uses this to drive a small percent indicator next to
        the Think/Tools toggles. The payload is intentionally tiny and
        emitted only when the underlying token count actually changes
        (callers should de-duplicate), so it adds no meaningful load to
        the SSE stream.

        Args:
            instance_id: The instance this usage snapshot belongs to.
            tokens: Estimated tokens currently in context (history + system prompt).
            context_window: Effective context window for the active model
                (already resolved against per-model overrides).
            model_name: Resolved model name (used for tooltip on the FE).
        """
        # Clamp percent to [0, 100] so a misbehaving estimator never produces
        # a negative or >100 indicator.
        percent = 0.0
        if context_window > 0:
            percent = max(0.0, min(100.0, (tokens / context_window) * 100.0))
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "context_usage",
            "tokens": int(tokens),
            "context_window": int(context_window),
            "percent": round(percent, 1),
            "model_name": model_name,
        }
        await self._stream_to_connections(instance_id, event)

    async def stream_instance_created(
        self,
        parent_id: str,
        instance_data: dict[str, Any],
    ) -> None:
        """Stream instance_created event to parent instance's connections.

        This is called when a child instance is created, notifying the parent's
        SSE listeners so they can update the instance tree immediately.

        Args:
            parent_id: The parent instance ID to stream to.
            instance_data: Full instance info dict with fields:
                instance_id, agent_id, parent_id, status, project_id,
                created_at, children, title.
        """
        event: dict[str, Any] = {
            "instance_id": parent_id,
            "event_type": "instance_created",
            "data": instance_data,
        }
        await self._stream_to_connections(parent_id, event)

    async def stream_lifecycle(
        self,
        instance_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Stream lifecycle event to active connections.

        Args:
            instance_id: The instance ID.
            event_type: Lifecycle event type (completed, failed, etc.).
            data: Optional event data.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": event_type,
        }
        if data:
            event["data"] = data
        await self._stream_to_connections(instance_id, event)

    async def stream_todo_update(
        self,
        instance_id: str,
        todos: list[dict],
    ) -> None:
        """Stream todo update event to all active connections.

        Emitted whenever the in-conversation todo graph changes so the
        frontend can re-render without a full reload. The method is
        intentionally agnostic to dict contents — it serializes whatever
        the todo manager hands it and pushes the payload through the
        SSE queue.

        Args:
            instance_id: The instance this todo update belongs to.
            todos: List of todo node dicts (frozen Phase 1 schema). Each
                dict has exactly seven keys:

                * ``id`` (str): Node identifier, always ``n-`` prefixed
                  (e.g., ``"n-a1b2c3d4"``). Stable across mutations.
                * ``index`` (int): Insertion-order position
                  (0-based). Preserved for backward compatibility with
                  pre-Phase-3 consumers that reference ``item.index``
                  (e.g., Angular ``track item.index``).
                * ``text`` (str): Human-readable description.
                * ``status`` (str): One of ``"pending"``,
                  ``"in_progress"``, or ``"done"``.
                * ``comment`` (str): User annotation side-channel.
                  Empty string when no comment is set.
                * ``next_ids`` (list[str]): Adjacency list of successor
                  node IDs. Empty list for terminal (sink) nodes.
                * ``subtasks`` (list[dict]): Checklist of sub-task dicts.
                  Each dict has three keys: ``id`` (s-prefixed),
                  ``text`` (description), ``status`` (pending|done —
                  binary).

                The dict shape is **frozen** across Phase 1 (manager)
                and Phase 1b (sub-tasks); schema evolved from six to
                seven keys and must not be changed without cross-phase
                coordination.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "todo_update",
            "todos": todos,
        }
        await self._stream_to_connections(instance_id, event)

    async def stream_question_pack(
        self,
        instance_id: str,
        pack: dict[str, Any],
    ) -> None:
        """Stream a question-pack event to all active connections.

        Emitted twice in the question lifecycle:

        1. From the ``question`` tool with ``status="pending"`` BEFORE
           the pause cascade — this is the only reliable emission point
           because the subsequent pause cascade cancels the graph task
           mid-execution, skipping any post-commit SSE code (F3 /
           SSE-timing note from phase1-plan).
        2. From the Phase 2 answer API with ``status="answered"`` BEFORE
           the resume cascade — same timing constraint for symmetry.

        Best-effort: failures during broadcast are logged at WARNING by
        :meth:`_stream_to_connections` (which silently drops events when
        no clients are connected). Callers (the ``question`` tool and
        the answer endpoint) wrap the await in their own try/except so
        a transport hiccup never blocks the question/answer flow.

        The payload follows the **frozen pack_to_dict schema** defined
        by :func:`daemon.services.question_manager.pack_to_dict`. The
        frontend consumes both pending and answered events with the
        same parser, so the schema is a contract across Phase 1 + Phase 2.

        Args:
            instance_id: The instance this question pack belongs to.
            pack: Question-pack dict (output of
                :func:`daemon.services.question_manager.pack_to_dict`).
                Carries ``instance_id``, ``status`` (``pending`` |
                ``answered``), ``created_at``, ``questions`` (list of
                per-question dicts), and ``answers``.
        """
        event: dict[str, Any] = {
            "instance_id": instance_id,
            "event_type": "question_pack",
            "message": pack,
        }
        await self._stream_to_connections(instance_id, event)

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def cleanup_instance(self, instance_id: str) -> None:
        """Remove all connections for an instance.
        
        Args:
            instance_id: The instance ID to clean up.
        """
        async with self._lock:
            self._connections.pop(instance_id, None)
            logger.debug(f"Cleaned up connections for {instance_id}")
    
    async def shutdown(self) -> None:
        """Shutdown the hub, clearing all connections."""
        async with self._lock:
            self._connections.clear()
        logger.info("LiveEventHub shutdown complete")
