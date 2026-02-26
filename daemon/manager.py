"""Session manager orchestrating all agent sessions."""

import uuid
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from langgraph.graph.state import CompiledStateGraph

# Create logger for this module
logger = logging.getLogger(__name__)

from .config import Config
from .graph import build_session_graph
from .loader import PromptCache, load_and_cache_prompt
from .persistence import (
    get_checkpointer,
    init_database,
    list_all_sessions,
    save_session_metadata,
    update_session_status,
    get_session_metadata,
    delete_all_sessions,
    get_session_messages,
    get_agent_name,
)
from .tools import create_session_tools
from .events import EventBroadcaster, Event

import asyncio
import logging
from .queue import InputMessageQueue, SessionWatchdog, SessionCircuitBreaker, QueuedMessage

import time
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

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


@dataclass
class MessageResult:
    """Result of sending a message to a session."""
    content: str
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class AsyncMessageResult:
    """Result of async message enqueue."""
    message_id: str
    session_id: str
    status: str = "queued"


class SessionManager:
    """Manages all agent sessions, their graphs, and lifecycle."""

    def __init__(self, config: Config):
        """Initialize the session manager.

        Args:
            config: Configuration object with LLM, limits, and persistence settings.
        """
        self.config = config
        self.conn = init_database(Path(config.persistence.db_path))
        self.checkpointer = get_checkpointer(self.conn)
        self.prompt_cache = PromptCache()
        # Maps session_id to tuple of (graph, agent_dir)
        self.sessions: dict[str, tuple[CompiledStateGraph, str]] = {}

        # NEW: Message queue system
        self.queue = InputMessageQueue(self.conn)
        self.watchdog = SessionWatchdog(self.queue, self.conn)
        self.circuit_breaker = SessionCircuitBreaker()
        self._processing: set[str] = set()  # sessions currently processing
        self._processing_lock = asyncio.Lock()
        
        # NEW: Event broadcaster for real-time SSE updates
        self.broadcaster = EventBroadcaster()

        # Start watchdog
        self.watchdog.start()

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

    def send_message(self, session_id: str, message: str) -> MessageResult:
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
        result = graph.invoke({"messages": [message]}, config)

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
                
                return MessageResult(
                    content=content,
                    thinking=thinking,
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
        # Check if already processing
        async with self._processing_lock:
            if session_id in self._processing:
                logger.debug(f"Session {session_id} already being processed, skipping")
                return
            self._processing.add(session_id)
        
        try:
            if not self.circuit_breaker.can_execute(session_id):
                logger.warning(f"Circuit breaker open for session {session_id}")
                return
            
            while True:
                msg = self.queue.dequeue(session_id, timeout=0)
                if msg is None:
                    break
                
                logger.info(f"Processing message {msg.message_id[:8]}... for session {session_id[:8]}...")
                
                # Extract retry flag from metadata
                is_retry = msg.metadata.get("is_retry", False) if msg.metadata else False
                
                # Broadcast status_changed event
                await self.broadcaster.broadcast(Event(
                    type="status_changed",
                    session_id=session_id,
                    message_id=msg.message_id,
                    data={"status": "processing", "is_retry": is_retry}
                ))
                
                try:
                    result = await asyncio.to_thread(
                        self._process_message_with_tracking,
                        session_id,
                        msg.content,
                        msg.message_id,
                        is_retry=is_retry,
                    )
                    
                    self.queue.ack(msg.message_id)
                    self.circuit_breaker.record_success(session_id)
                    
                    # Broadcast completed event
                    await self.broadcaster.broadcast(Event(
                        type="completed",
                        session_id=session_id,
                        message_id=msg.message_id,
                        data={
                            "content": result.content,
                            "thinking": result.thinking,
                            "tool_calls": result.tool_calls,
                        }
                    ))
                    
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
            
            # Queue is empty - check if this is a child session and send completion report
            if self.queue.is_empty(session_id):
                meta = get_session_metadata(self.conn, session_id)
                if meta and meta.get("parent_id"):
                    # This is a child session that has completed - send report to parent
                    await self._send_completion_report(session_id)
        finally:
            async with self._processing_lock:
                self._processing.discard(session_id)

    def _process_message_sync(self, session_id: str, message: str) -> MessageResult:
        """Synchronous message processing (wraps existing send_message logic)."""
        return self.send_message(session_id, message)

    def _process_message_with_tracking(
        self, 
        session_id: str, 
        message: str,
        message_id: str,
        is_retry: bool = False,
    ) -> MessageResult:
        """Process message with activity tracking.
        
        On retry, resumes from checkpoint instead of re-sending message
        to prevent duplicate execution.
        
        Args:
            session_id: The session ID.
            message: The message content.
            message_id: The queue message ID.
            is_retry: If True, attempt to resume from checkpoint.
        
        Returns:
            MessageResult with response data.
        """
        graph = self.get_session(session_id)
        
        # Create activity callback for this message
        activity_callback = ActivityCallbackHandler(
            self.queue, 
            message_id,
            update_interval_seconds=5.0
        )
        
        config = {
            "configurable": {"thread_id": session_id},
            "callbacks": [activity_callback]
        }
        
        # On retry with checkpoint, resume instead of re-adding message
        if is_retry and self._has_checkpoint(session_id):
            logger.info(f"Resuming session {session_id[:8]}... from checkpoint (retry)")
            result = graph.invoke(None, config)
        else:
            # First attempt or no checkpoint - add message to conversation
            result = graph.invoke({"messages": [message]}, config)
        
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
                
                return MessageResult(
                    content=content,
                    thinking=thinking,
                    tool_calls=tool_calls,
                )
        
        return MessageResult(content="")

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
        messages = get_session_messages(self.conn, session_id)
        
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

    async def _send_completion_report(self, session_id: str) -> None:
        """Send completion report to parent session when child is done.
        
        Called when a child session's queue becomes empty.
        Summarizes the child's activity and enqueues a report message to the parent.
        
        Args:
            session_id: The child session ID that has completed.
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
        
        # Summarize session activity
        summary = await self._summarize_session(session_id, agent_name)
        
        # Enqueue report message to parent
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

    def get_queue_stats(self, session_id: str):
        """Get queue statistics for a session."""
        return self.queue.get_stats(session_id)

    def _has_checkpoint(self, session_id: str) -> bool:
        """Check if a checkpoint exists for this session.
        
        Args:
            session_id: The session ID to check.
            
        Returns:
            True if checkpoint exists, False otherwise.
        """
        try:
            config = {"configurable": {"thread_id": session_id}}
            # Get the current state from checkpointer
            state = self.checkpointer.get(config)
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

    def list_sessions(self) -> list[dict]:
        """List all sessions.

        Returns:
            List of session info dictionaries from the database.
        """
        return list_all_sessions(self.conn)

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

    def get_messages(self, session_id: str) -> list[dict]:
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
        
        return get_session_messages(self.conn, session_id)

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
