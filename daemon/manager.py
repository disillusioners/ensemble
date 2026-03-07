"""Session manager orchestrating all agent sessions."""

import uuid
import logging
import asyncio
import sqlite3
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from langgraph.graph.state import CompiledStateGraph
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from .config import Config
from .graph import build_session_graph
from .loader import PromptCache, load_and_cache_prompt
from .persistence import (
    get_checkpointer,
    init_database,
    list_all_sessions,
    save_session_metadata,
    update_session_status,
    update_session_title,
    get_session_metadata,
    delete_all_sessions,
    get_session_messages,
    get_agent_name,
)
from .tools import create_session_tools
from .events import EventBroadcaster, Event
from .sources import SourceRegistry, ResponseDispatcher, SourceCleanup
from .queue import InputMessageQueue, SessionWatchdog, SessionCircuitBreaker, QueuedMessage
from .cancellation import (
    CancellationToken, 
    CancellationReason,
    OperationCancelledError
)
from .request_registry import ActiveRequestRegistry

logger = logging.getLogger(__name__)


class ActivityCallbackHandler(BaseCallbackHandler):
    """Callback to update message activity during LLM/graph execution.
    
    This ensures long-running tasks are not incorrectly marked as "stuck"
    by the watchdog, as long as there's recent activity.
    """
    
    def __init__(self, queue, message_id: str, update_interval_seconds: float = 5.0):
        self.queue = queue
        self.message_id = message_id
        self.update_interval = update_interval_seconds
        self._last_update = time.monotonic()
    
    def _maybe_update(self) -> None:
        """Throttled activity update to avoid excessive DB writes."""
        now = time.monotonic()
        if now - self._last_update >= self.update_interval:
            try:
                self.queue.update_activity(self.message_id)
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
    """Result of sending a message to a session."""
    content: str
    thinking: str | None = None
    thinking_extracted: str | None = None  # Extracted from <think/> tags in content
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class AsyncMessageResult:
    """Result of async message enqueue."""
    message_id: str
    session_id: str
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


class SessionManager:
    """Manages all agent sessions, their graphs, and lifecycle."""

    def __init__(self, config: Config):
        """Initialize the session manager.

        Args:
            config: Configuration object with LLM, limits, and persistence settings.
        """
        self.config = config
        self.conn = init_database(Path(config.persistence.db_path))
        self.db_path = Path(config.persistence.db_path)
        self._checkpointer = None  # Lazy init - call await manager.initialize() to set
        self.prompt_cache = PromptCache()
        # Maps session_id to tuple of (graph, agent_dir)
        self.sessions: dict[str, tuple[CompiledStateGraph, str]] = {}

        # NEW: Message queue system
        self.queue = InputMessageQueue(self.conn)
        
        # NEW: Request registry for cancellation support
        self._request_registry = ActiveRequestRegistry()
        
        self.watchdog = SessionWatchdog(
            self.queue, 
            self.conn,
            request_registry=self._request_registry
        )
        self.circuit_breaker = SessionCircuitBreaker()
        self._processing: set[str] = set()  # sessions currently processing
        self._processing_lock = asyncio.Lock()
        
        # NEW: Event broadcaster for real-time SSE updates
        self.broadcaster = EventBroadcaster()

        # NEW: Pluggable message sources system
        self.source_registry = SourceRegistry(conn=self.conn, manager=self)
        self.source_dispatcher = ResponseDispatcher(
            broadcaster=self.broadcaster,
            registry=self.source_registry,
            subscriber_id="response_dispatcher"
        )
        self._source_cleanup: SourceCleanup | None = None

        # Start watchdog
        self.watchdog.start()

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
        """
        self._checkpointer = await get_checkpointer(self.db_path)
        logger.info("SessionManager initialized with async checkpointer")

    def spawn_session(
        self, agent_dir: str, session_id: str | None = None, parent_id: str | None = None
    ) -> str:
        """Create a new agent session.

        Args:
            agent_dir: Path to the agent directory.
            session_id: Optional session ID. Auto-generated if not provided.
            parent_id: Optional parent session ID for hierarchical sessions.

        Returns:
            The session_id of the newly created session.

        Raises:
            ValueError: If max_sessions or max_children_per_session limit is exceeded.
        """
        # Generate session_id if not provided
        if session_id is None:
            session_id = str(uuid.uuid4())

        # Check max_sessions limit
        current_session_count = len(self.sessions)
        if current_session_count >= self.config.limits.max_sessions:
            raise ValueError(
                f"Max sessions limit reached: {self.config.limits.max_sessions}"
            )

        # Check max_children_per_session limit if parent_id is provided
        if parent_id is not None:
            parent_meta = get_session_metadata(self.conn, parent_id)
            if parent_meta and "children" in parent_meta:
                child_count = len(parent_meta["children"])
                if child_count >= self.config.limits.max_children_per_session:
                    raise ValueError(
                        f"Max children per session limit reached: "
                        f"{self.config.limits.max_children_per_session}"
                    )

        # Load and cache prompt
        agent_path = Path(agent_dir)
        system_prompt, token_count = load_and_cache_prompt(agent_path, self.prompt_cache)

        # Create tools with this manager reference
        tools = create_session_tools(self, session_id, agent_dir)

        # Build LLM config
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "temperature": self.config.llm.temperature,
        }

        # Build retry config from queue settings
        retry_config = {
            "max_retries": self.config.queue.llm_max_retries,
        }

        # Build graph with checkpointer
        graph = build_session_graph(
            tools=tools,
            checkpointer=self.checkpointer,
            llm_config=llm_config,
            system_prompt=system_prompt,
            retry_config=retry_config,
        )

        # Save metadata to DB
        save_session_metadata(
            conn=self.conn,
            session_id=session_id,
            agent_dir=agent_dir,
            parent_id=parent_id,
        )

        # Store in sessions dict
        self.sessions[session_id] = (graph, agent_dir)

        return session_id

    async def send_message(self, session_id: str, message: str) -> MessageResult:
        """Send a message to a session and get the response.

        Args:
            session_id: The ID of the session to send the message to.
            message: The message content to send.

        Returns:
            MessageResult with content, thinking, and tool_calls.

        Raises:
            KeyError: If session_id is not found.
        """
        # Get session graph (will lazy-load from DB if needed)
        graph = self.get_session(session_id)

        # Invoke with message
        config = {"configurable": {"thread_id": session_id}}
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
        session_id: str, 
        message: str, 
        source: str = "api",
        priority: int = 1
    ) -> AsyncMessageResult:
        """Enqueue a message for a session (non-blocking).
        
        Args:
            session_id: The ID of the target session.
            message: The message content.
            source: Source identifier (e.g., "api", "web", "agent:<id>").
            priority: Message priority (0=system, 1=user).
        
        Returns:
            AsyncMessageResult with message_id and status.
        """
        # Check session exists
        self.get_session(session_id)  # raises KeyError if not found
        
        # Enqueue the message
        message_id = self.queue.enqueue(
            session_id=session_id,
            content=message,
            source=source,
            priority=priority
        )
        
        # Broadcast message_queued event
        await self.broadcaster.broadcast(Event(
            type="message_queued",
            session_id=session_id,
            message_id=message_id,
            data={
                "content": message,
                "source": source,
                "priority": priority,
                "status": "queued"
            }
        ))
        
        logger.debug(f"Enqueued message {message_id} for session {session_id}")
        
        # Trigger async processing with error handling
        task = asyncio.create_task(self._process_queue(session_id))
        task.add_done_callback(lambda t: self._handle_queue_task_done(t, session_id))
        
        return AsyncMessageResult(
            message_id=message_id,
            session_id=session_id,
            status="queued"
        )

    def _handle_queue_task_done(self, task: asyncio.Task, session_id: str) -> None:
        """Callback for when _process_queue task completes.
        
        Logs any exceptions that occurred during processing.
        
        Args:
            task: The completed task.
            session_id: The session ID that was being processed.
        """
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Queue processing task failed for session {session_id}: {exc}")
        except asyncio.CancelledError:
            logger.debug(f"Queue processing task cancelled for session {session_id}")

    async def _process_queue(self, session_id: str) -> None:
        """Event-driven queue processor for a session."""
        logger.debug(f"_process_queue called for session {session_id[:8]}...")
        # Check if already processing
        async with self._processing_lock:
            if session_id in self._processing:
                logger.debug(f"Session {session_id[:8]}... already being processed, skipping")
                return
            self._processing.add(session_id)
            logger.debug(f"Added session {session_id[:8]}... to processing set")
        
        try:
            if not self.circuit_breaker.can_execute(session_id):
                logger.warning(f"Circuit breaker open for session {session_id[:8]}...")
                return
            
            logger.debug(f"Starting dequeue loop for session {session_id[:8]}...")
            while True:
                msg = self.queue.dequeue(session_id, timeout=0)
                if msg is None:
                    logger.debug(f"No more messages for session {session_id[:8]}..., exiting loop")
                    break
                
                logger.info(f"Processing message {msg.message_id[:8]}... for session {session_id[:8]}...")
                
                # Check if this is the first message and generate title
                # Get message count before processing this message
                existing_messages = await get_session_messages(self.checkpointer, session_id)
                is_first_message = len(existing_messages) == 0
                
                # Extract retry flag from metadata
                is_retry = msg.metadata.get("is_retry", False) if msg.metadata else False
                
                # Broadcast status_changed event
                await self.broadcaster.broadcast(Event(
                    type="status_changed",
                    session_id=session_id,
                    message_id=msg.message_id,
                    data={"status": "processing", "is_retry": is_retry}
                ))
                
                # Register request for cancellation support
                cancellation_source = self._request_registry.register(
                    message_id=msg.message_id,
                    session_id=session_id,
                    task=asyncio.current_task()
                )
                
                try:
                    result = await self._process_message_with_tracking(
                        session_id,
                        msg.content,
                        msg.message_id,
                        cancellation_token=cancellation_source.token,
                        is_retry=is_retry,
                    )
                    
                    # Pre-ACK status check to prevent race condition with watchdog
                    # Always record success since processing completed without error
                    self.circuit_breaker.record_success(session_id)
                    
                    cursor = self.conn.execute(
                        "SELECT status FROM message_queue WHERE message_id = ?",
                        (msg.message_id,)
                    )
                    row = cursor.fetchone()
                    if row and row[0] == 'processing':
                        self.queue.ack(msg.message_id)
                    else:
                        logger.warning(
                            f"Message {msg.message_id[:8]}... status changed to '{row[0] if row else 'unknown'}' "
                            f"during processing, skipping ack (success already recorded)"
                        )
                    
                    # Generate title if this was the first message
                    if is_first_message:
                        try:
                            title = await self._generate_session_title(session_id, msg.content)
                            if title:
                                # Broadcast title_updated event for frontend refresh
                                await self.broadcaster.broadcast(Event(
                                    type="title_updated",
                                    session_id=session_id,
                                    message_id=msg.message_id,
                                    data={"title": title}
                                ))
                        except Exception as e:
                            logger.warning(f"Failed to generate title for session {session_id}: {e}")
                    
                    # Broadcast completed event
                    await self.broadcaster.broadcast(Event(
                        type="completed",
                        session_id=session_id,
                        message_id=msg.message_id,
                        data={
                            "content": result.content,
                            "thinking": result.thinking,
                            "thinking_extracted": result.thinking_extracted,
                            "tool_calls": result.tool_calls,
                            "source": msg.source,  # Required for ResponseDispatcher routing
                        }
                    ))
                    
                except OperationCancelledError as e:
                    logger.info(f"Message {msg.message_id[:8]}... was cancelled: {e.reason.value}")
                    # Don't schedule retry here - watchdog already did
                    # Broadcast cancelled event
                    await self.broadcaster.broadcast(Event(
                        type="cancelled",
                        session_id=session_id,
                        message_id=msg.message_id,
                        data={"reason": e.reason.value}
                    ))
                    
                except asyncio.CancelledError:
                    logger.info(f"Message {msg.message_id[:8]}... task was cancelled")
                    raise  # Re-raise to properly handle task cancellation
                    
                except Exception as e:
                    logger.error(f"Error processing message {msg.message_id}: {e}")
                    self.circuit_breaker.record_failure(session_id)
                    
                    if msg.retry_count < self.config.queue.max_retries:
                        self.queue.schedule_retry(
                            msg.message_id,
                            msg.retry_count + 1,
                            str(e)
                        )
                        # Broadcast retry scheduled event
                        await self.broadcaster.broadcast(Event(
                            type="status_changed",
                            session_id=session_id,
                            message_id=msg.message_id,
                            data={
                                "status": "retrying",
                                "retry_count": msg.retry_count + 1,
                                "error": str(e)
                            }
                        ))
                    else:
                        self.queue.fail(msg.message_id, str(e))
                        # Broadcast error event
                        await self.broadcaster.broadcast(Event(
                            type="error",
                            session_id=session_id,
                            message_id=msg.message_id,
                            data={
                                "error": str(e),
                                "status": "failed",
                                "retry_count": msg.retry_count
                            }
                        ))
                
                finally:
                    # Always unregister the request
                    self._request_registry.unregister(msg.message_id)
            
            # Queue is empty - check if this is a child session and send completion report
            if self.queue.is_empty(session_id):
                meta = get_session_metadata(self.conn, session_id)
                if meta and meta.get("parent_id"):
                    # This is a child session that has completed - send report to parent
                    await self._send_completion_report(session_id)
        finally:
            async with self._processing_lock:
                self._processing.discard(session_id)
                logger.debug(f"Removed session {session_id[:8]}... from processing set")

    def _process_message_sync(self, session_id: str, message: str) -> MessageResult:
        """Synchronous message processing (wraps existing send_message logic)."""
        return self.send_message(session_id, message)

    async def _process_message_with_tracking(
        self, 
        session_id: str, 
        message: str,
        message_id: str,
        cancellation_token: CancellationToken | None = None,
        is_retry: bool = False,
    ) -> MessageResult:
        """Process message with activity tracking and cancellation support.
        
        On retry, resumes from checkpoint instead of re-sending message
        to prevent duplicate execution.
        
        Args:
            session_id: The session ID.
            message: The message content.
            message_id: The queue message ID.
            cancellation_token: Optional token to check for cancellation.
            is_retry: If True, attempt to resume from checkpoint.
        
        Returns:
            MessageResult with response data.
            
        Raises:
            OperationCancelledError: If cancellation is requested.
        """
        graph = self.get_session(session_id)
        
        # Create activity callback for this message
        activity_callback = ActivityCallbackHandler(
            self.queue, 
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
            "configurable": {"thread_id": session_id},
            "callbacks": callbacks
        }
        
        # Variables to collect during streaming
        all_tool_calls = []
        tool_call_map = {}  # Track tool calls by ID to match with outputs
        thinking_content = None
        final_content = ""
        
        # Content chunk batching to reduce event rate
        content_buffer = ""
        content_buffer_size = 0
        CONTENT_BATCH_THRESHOLD = 50  # Flush after 50 characters
        CONTENT_BATCH_TIMEOUT = 0.05  # Or after 50ms (whichever comes first)
        last_content_flush = time.monotonic()  # Initialize to current time
        
        # Adaptive batching settings (adjusted based on queue health)
        adaptive_threshold = CONTENT_BATCH_THRESHOLD
        adaptive_timeout = CONTENT_BATCH_TIMEOUT
        
        # Event counter for monitoring
        event_count = 0
        
        # Build input - on retry with checkpoint, resume from None
        if is_retry and await self._has_checkpoint(session_id):
            logger.info(f"Resuming session {session_id[:8]}... from checkpoint (retry)")
            graph_input = None  # LangGraph will resume from checkpoint
        else:
            # First attempt or no checkpoint - add message to conversation
            graph_input = {"messages": [message]}
        
        # Stream through graph execution
        # When using multiple stream modes, events are tuples: (mode, data)
        try:
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
                            if hasattr(latest_msg, 'thinking') and latest_msg.thinking:
                                thinking_content = latest_msg.thinking
                                
                                # Broadcast thinking event
                                await self.broadcaster.broadcast(Event(
                                    type="thinking",
                                    session_id=session_id,
                                    message_id=message_id,
                                    data={"content": thinking_content}
                                ))
                            
                            # Track tool calls from AI message for matching
                            if hasattr(latest_msg, 'tool_calls') and latest_msg.tool_calls:
                                for tc in latest_msg.tool_calls:
                                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                                    tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                    
                                    # Store for matching with tool output
                                    tool_call_map[tc_id] = {
                                        "name": tc_name,
                                        "args": tc_args,
                                    }
                                    
                                    # Broadcast tool_call event (tool starting)
                                    await self.broadcaster.broadcast(Event(
                                        type="tool_call",
                                        session_id=session_id,
                                        message_id=message_id,
                                        data={
                                            "id": tc_id,
                                            "name": tc_name,
                                            "arguments": tc_args,
                                        }
                                    ))
                    
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
                            await self.broadcaster.broadcast(Event(
                                type="tool_complete",
                                session_id=session_id,
                                message_id=message_id,
                                data=tool_call_data
                            ))
                
                elif mode == "messages":
                    # Handle token-level streaming with adaptive batching to reduce event rate
                    # data is a tuple: (message_chunk, metadata)
                    if isinstance(data, tuple) and len(data) == 2:
                        chunk, metadata = data
                        if hasattr(chunk, 'content') and chunk.content:
                            content_buffer += chunk.content
                            content_buffer_size += len(chunk.content)
                            event_count += 1
                            
                            # Flush if buffer exceeds threshold OR timeout elapsed
                            now = time.monotonic()
                            should_flush = (
                                content_buffer_size >= adaptive_threshold or
                                (now - last_content_flush) >= adaptive_timeout
                            )
                            
                            if should_flush and content_buffer:
                                await self.broadcaster.broadcast(Event(
                                    type="content_chunk",
                                    session_id=session_id,
                                    message_id=message_id,
                                    data={"chunk": content_buffer}
                                ))
                                content_buffer = ""
                                content_buffer_size = 0
                                last_content_flush = now
                                
                            # Adaptive batching: check queue health periodically
                            if event_count % 20 == 0:
                                stats = self.broadcaster.get_stats(session_id)
                                queue_fill_ratio = stats["queue_size"] / stats.get("max_queue_size", 200)
                                
                                # Increase batch size when queue is > 50% full
                                if queue_fill_ratio > 0.5:
                                    adaptive_threshold = CONTENT_BATCH_THRESHOLD * 2  # 100 chars
                                    adaptive_timeout = CONTENT_BATCH_TIMEOUT * 2     # 100ms
                                    if event_count == 20:  # Log once
                                        logger.info(
                                            f"Queue at {queue_fill_ratio:.0%} capacity, "
                                            f"increasing batch size for session {session_id[:8]}"
                                        )
                                else:
                                    adaptive_threshold = CONTENT_BATCH_THRESHOLD
                                    adaptive_timeout = CONTENT_BATCH_TIMEOUT
            
            # Flush any remaining content in buffer after streaming ends
            if content_buffer:
                await self.broadcaster.broadcast(Event(
                    type="content_chunk",
                    session_id=session_id,
                    message_id=message_id,
                    data={"chunk": content_buffer}
                ))
                logger.debug(f"Flushed final content chunk batch: {len(content_buffer)} chars")
                
        except Exception as e:
            logger.error(f"Streaming failed for message {message_id}: {e}")
            # Broadcast error event
            await self.broadcaster.broadcast(Event(
                type="error",
                session_id=session_id,
                message_id=message_id,
                data={"error": str(e), "stage": "streaming"}
            ))
            raise  # Re-raise to let _process_queue handle retry logic
        
        # After streaming completes, get final result
        # Validate final_result exists
        final_result = await graph.aget_state(config)
        if not final_result:
            logger.error(f"No final state for session {session_id} after streaming")
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
                if kwargs.get("thinking"):
                    thinking_content = kwargs["thinking"]
        
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

    async def _summarize_session(self, session_id: str, agent_name: str) -> str:
        """Summarize session messages using LLM.
        
        Args:
            session_id: The session ID to summarize.
            agent_name: The name of the agent (e.g., "Coder", "Designer").
            
        Returns:
            Formatted summary string: "{agent_name} has done: {summary}"
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
        
        # Get session messages
        messages = await get_session_messages(self.checkpointer, session_id)
        
        if not messages:
            return f"{agent_name} has done: No activity recorded."
        
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
            return f"{agent_name} has done: No messages to summarize."
        
        conversation = "\n".join(conversation_text)
        
        # Create LLM client for summarization using the same config pattern
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "temperature": 0.3,  # Lower temperature for more focused summaries
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
            return f"{agent_name} has done: {summary}"
        except Exception as e:
            logger.warning(f"Failed to summarize session {session_id}: {e}")
            # Fallback: count messages and provide basic summary
            return f"{agent_name} has done: Completed {len(messages)} message(s)."

    async def _send_completion_report(self, session_id: str, use_llm_summary: bool = False) -> None:
        """Send completion report to parent session when child is done.
        
        Called when a child session's queue becomes empty.
        Sends the child's last assistant message (or LLM summary) to the parent.
        
        Args:
            session_id: The child session ID that has completed.
            use_llm_summary: If True, use LLM to summarize. Default: False (use last message).
        """
        # Get session metadata
        meta = get_session_metadata(self.conn, session_id)
        if not meta:
            logger.warning(f"Cannot send completion report: session {session_id} not found")
            return
        
        parent_id = meta.get("parent_id")
        if not parent_id:
            logger.debug(f"Session {session_id} has no parent, skipping completion report")
            return
        
        agent_name = meta.get("agent_name") or get_agent_name(meta.get("agent_dir", "Unknown"))
        
        logger.info(f"Session {session_id[:8]}... completed, sending report to parent {parent_id[:8]}...")
        
        # Get report content - either last message or LLM summary
        if use_llm_summary:
            summary = await self._summarize_session(session_id, agent_name)
        else:
            summary = await self._get_last_assistant_message(session_id, agent_name)
        
        # Enqueue report message to parent
        from .queue import InputMessageQueue
        message_id = self.queue.enqueue(
            session_id=parent_id,
            content=summary,
            source=f"report:{session_id}",
            priority=1,  # Normal priority as requested
            metadata={"type": "completion_report", "child_session_id": session_id}
        )
        
        # Broadcast report event
        await self.broadcaster.broadcast(Event(
            type="status_changed",
            session_id=parent_id,
            message_id=message_id,
            data={
                "type": "completion_report",
                "child_session_id": session_id,
                "agent_name": agent_name,
                "summary": summary
            }
        ))
        
        logger.info(f"Sent completion report from {agent_name} ({session_id[:8]}...) to parent ({parent_id[:8]}...)")
        
        # Trigger parent queue processing
        asyncio.create_task(self._process_queue(parent_id))

    async def _get_last_assistant_message(self, session_id: str, agent_name: str) -> str:
        """Get the last assistant message from session history.
        
        This is the default/simple approach for completion reports - just
        pass the agent's last response to the parent.
        
        Args:
            session_id: The session ID to get message from.
            agent_name: The name of the agent (e.g., "Coder", "Designer").
            
        Returns:
            Formatted string: "{agent_name} has done: {last_message}"
        """
        messages = await get_session_messages(self.checkpointer, session_id)
        
        # Find the last assistant message
        last_assistant_content = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if content and content.strip():
                    last_assistant_content = content.strip()
                    break
        
        if last_assistant_content:
            return f"{agent_name} has done:\n{last_assistant_content}"
        else:
            # Fallback if no assistant message found
            return f"{agent_name} has done: Task completed (no response message)."

        
    async def _generate_session_title(self, session_id: str, first_message: str) -> str | None:
        """Generate a session title from the first user message.
        
        Uses LLM to generate a concise, descriptive title based on the first message.
        The title is stored in the session metadata.
        
        Args:
            session_id: The session ID to generate title for.
            first_message: The first user message content.
            
        Returns:
            Generated title string, or None if generation fails.
        """
        # Skip if empty message
        if not first_message or not first_message.strip():
            return None
        
        # Check if title already exists
        meta = get_session_metadata(self.conn, session_id)
        if meta and meta.get("metadata", {}).get("title"):
            # Title already exists, skip
            logger.debug(f"Title already exists for session {session_id}, skipping generation")
            return None
        
        from langchain_core.messages import HumanMessage, SystemMessage
        
        # Create LLM client for title generation
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "temperature": 0.3,  # Lower temperature for more focused titles
        }
        
        # Import here to use the same pattern as graph.py
        from .graph import ThinkingChatOpenAI
        llm = ThinkingChatOpenAI(**llm_config)
        
        title_prompt = f"""Generate a short, descriptive title (3-6 words max) for this user message. The title should summarize what the user is asking about or trying to accomplish.

User message:
{first_message[:500]}

Title:"""

        try:
            response = await asyncio.to_thread(
                llm.invoke,
                [SystemMessage(content="You are a helpful assistant that generates concise session titles."),
                 HumanMessage(content=title_prompt)]
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
                return None
            
            # Truncate to reasonable length (100 chars max)
            if len(title) > 100:
                title = title[:97] + "..."
            
            # Store title in session metadata
            update_session_title(self.conn, session_id, title)
            logger.info(f"Generated title for session {session_id}: {title}")
            return title
            
        except Exception as e:
            logger.warning(f"Failed to generate title for session {session_id}: {e}")
            return None

    def get_queue_stats(self, session_id: str):
        """Get queue statistics for a session."""
        return self.queue.get_stats(session_id)

    async def _has_checkpoint(self, session_id: str) -> bool:
        """Check if a checkpoint exists for this session.
        
        Args:
            session_id: The session ID to check.
            
        Returns:
            True if checkpoint exists, False otherwise.
        """
        try:
            config = {"configurable": {"thread_id": session_id}}
            # Get the current state from async checkpointer
            state = await self.checkpointer.aget(config)
            return state is not None
        except Exception:
            return False

    def terminate_session(self, session_id: str) -> bool:
        """Terminate a session.

        Args:
            session_id: The ID of the session to terminate.

        Returns:
            True if termination was successful, False if session was not found.
        """
        # Remove from processing set
        self._processing.discard(session_id)
        
        # Clean up event broadcaster
        self.broadcaster.cleanup_session(session_id)

        # Remove from sessions dict
        if session_id in self.sessions:
            del self.sessions[session_id]
        else:
            return False

        # Update DB status to terminated
        update_session_status(self.conn, session_id, "terminated")

        return True

    def get_session(self, session_id: str) -> CompiledStateGraph:
        """Get a session graph instance.

        Uses database as source of truth. If session exists in DB but not in memory,
        it will be restored (lazy loading).

        Args:
            session_id: The ID of the session.

        Returns:
            The CompiledStateGraph instance for the session.

        Raises:
            KeyError: If session_id is not found in database.
        """
        # Check in-memory cache first
        if session_id in self.sessions:
            graph, _ = self.sessions[session_id]
            return graph

        # Not in memory - check database and restore if found
        meta = get_session_metadata(self.conn, session_id)
        if meta is None:
            raise KeyError(f"Session not found: {session_id}")

        # Session exists in DB but not in memory - restore it
        return self._restore_session(session_id, meta["agent_dir"])

    def _restore_session(self, session_id: str, agent_dir: str) -> CompiledStateGraph:
        """Restore a session from database into memory.

        Rebuilds the graph with the same session_id. The checkpointer will
        restore conversation state from LangGraph's checkpoint tables.

        Args:
            session_id: The ID of the session to restore.
            agent_dir: Path to the agent directory.

        Returns:
            The restored CompiledStateGraph instance.
        """
        # Load and cache prompt
        agent_path = Path(agent_dir)
        system_prompt, token_count = load_and_cache_prompt(agent_path, self.prompt_cache)

        # Create tools with this manager reference
        tools = create_session_tools(self, session_id, agent_dir)

        # Build LLM config
        llm_config = {
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
            "temperature": self.config.llm.temperature,
        }

        # Build retry config from queue settings
        retry_config = {
            "max_retries": self.config.queue.llm_max_retries,
        }

        # Build graph with checkpointer (will restore state from checkpoints)
        graph = build_session_graph(
            tools=tools,
            checkpointer=self.checkpointer,
            llm_config=llm_config,
            system_prompt=system_prompt,
            retry_config=retry_config,
        )

        # Store in sessions dict
        self.sessions[session_id] = (graph, agent_dir)

        return graph

    def list_sessions(self, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        """List sessions with pagination.

        Args:
            limit: Maximum number of sessions to return (default: 100).
            offset: Number of sessions to skip (default: 0).

        Returns:
            Tuple of (list of session info dictionaries, total count).
        """
        return list_all_sessions(self.conn, limit=limit, offset=offset)

    def get_session_info(self, session_id: str) -> dict:
        """Get information about a specific session.

        Args:
            session_id: The ID of the session.

        Returns:
            Session metadata dictionary from the database.

        Raises:
            KeyError: If session is not found.
        """
        meta = get_session_metadata(self.conn, session_id)
        if meta is None:
            raise KeyError(f"Session not found: {session_id}")
        return meta

    async def get_messages(self, session_id: str) -> list[dict]:
        """Get message history for a session.

        Args:
            session_id: The ID of the session.

        Returns:
            List of message dictionaries from LangGraph checkpoints.

        Raises:
            KeyError: If session is not found.
        """
        # Verify session exists
        self.get_session(session_id)  # raises KeyError if not found
        
        return await get_session_messages(self.checkpointer, session_id)

    def clear_all_sessions(self) -> int:
        """Clear all sessions from memory and database.

        Returns:
            Number of sessions deleted from database.
        """
        # Clear processing set
        self._processing.clear()

        # Clear in-memory sessions
        self.sessions.clear()

        # Clear database sessions
        return delete_all_sessions(self.conn)
    
    async def start_sources(self) -> None:
        """Start the pluggable message sources system.
        
        This initializes:
        - SourceRegistry: Loads and starts all enabled adapters from DB
        - ResponseDispatcher: Listens for completed events to route responses
        - SourceCleanup: Periodic cleanup of old processed messages and mappings
        """
        # Start cleanup job
        self._source_cleanup = SourceCleanup(self.conn)
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
