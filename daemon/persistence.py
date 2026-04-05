"""
Persistence layer with SQLite for LangGraph checkpointing.

Threading Notes:
- AsyncSqliteSaver uses aiosqlite which runs SQLite operations in a background thread pool.
- This is safe because aiosqlite manages thread isolation internally.
- The checkpointer operates independently from the main SQLAlchemy session used by repositories.
- No additional synchronization is needed between checkpointing and repository operations since they
  use separate connections.
"""

import logging
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)


async def get_checkpointer(db_path: Path) -> AsyncSqliteSaver:
    """Create and return an AsyncSqliteSaver checkpointer.
    
    Threading Model:
    - AsyncSqliteSaver uses aiosqlite internally, which runs SQLite operations in a dedicated
      background thread pool managed by asyncio's default executor.
    - All database operations are offloaded from the main event loop, so they don't block
      async execution.
    - aiosqlite handles thread isolation internally - you don't need to manage locks or use
      run_in_executor() yourself.
    
    Lifecycle:
    - This creates the aiosqlite connection directly which will be kept alive for the entire
      application lifetime.
    - The checkpointer will be cleaned up when the application shuts down via the
      cleanup_checkpointer() method on the InstanceManager class.
    
    Independence:
    - The checkpointer uses its own connection to the SQLite database, separate from any
      SQLAlchemy sessions used by repositories.
    - No additional synchronization is needed between checkpointing and repository operations
      since they use separate connections.
    
    Args:
        db_path: Path to the SQLite database file.
        
    Returns:
        AsyncSqliteSaver: LangGraph async checkpointer instance.
    """
    # Create connection directly - don't use async context manager
    conn = await aiosqlite.connect(str(db_path))
    return AsyncSqliteSaver(conn)


async def get_instance_messages(
    checkpointer: AsyncSqliteSaver,
    instance_id: str
) -> list[dict[str, Any]]:
    """Get message history from LangGraph checkpoints.
    
    Args:
        checkpointer: Shared AsyncSqliteSaver instance.
        instance_id: Instance identifier to retrieve messages for.
        
    Returns:
        List of message dictionaries with role, content, thinking, tool_calls.
    """
    from datetime import datetime, timezone
    import uuid
    
    # Use the PASSED checkpointer instead of creating a new one
    config = {"configurable": {"thread_id": instance_id}}
    
    # Get the current state from async checkpointer
    state = await checkpointer.aget(config)
    if state is None:
        return []
    
    # LangGraph stores messages in channel_values
    channel_values = state.get("channel_values", {})
    messages = channel_values.get("messages", [])
    if not messages:
        return []
    
    result = []
    
    # Build a map of tool_call_id -> output from ToolMessages
    tool_outputs = {}
    for msg in messages:
        if hasattr(msg, 'tool_call_id'):  # It's a ToolMessage
            tool_outputs[msg.tool_call_id] = msg.content
    
    for msg in messages:
        msg_type = getattr(msg, 'type', 'unknown')
        
        # Map message types to roles
        role_map = {
            'human': 'user',
            'ai': 'assistant',
            'system': 'system',
            'tool': 'tool',
        }
        role = role_map.get(msg_type, msg_type)
        
        # Skip tool messages in the main list (they're included in tool_calls)
        if msg_type == 'tool':
            continue
        
        content = getattr(msg, 'content', '') or ''
        
        # Extract thinking from additional_kwargs
        thinking = None
        if hasattr(msg, 'additional_kwargs'):
            kwargs = msg.additional_kwargs or {}
            if kwargs.get("thinking"):
                thinking = kwargs["thinking"]
            elif kwargs.get("reasoning_content"):
                thinking = kwargs["reasoning_content"]
        
        # Parse <think/> tags from content using shared utility
        from daemon.manager import parse_think_tags
        content, thinking_extracted = parse_think_tags(content)
        
        # Extract tool_calls for AI messages
        tool_calls = None
        if msg_type == 'ai' and hasattr(msg, 'tool_calls') and msg.tool_calls:
            tool_calls = []
            for tc in msg.tool_calls:
                # Handle both dict and object formats
                if isinstance(tc, dict):
                    tc_id = tc.get("id", "")
                    tool_calls.append({
                        "id": tc_id,
                        "name": tc.get("name", ""),
                        "arguments": tc.get("args", {}),
                        "output": tool_outputs.get(tc_id),
                    })
                else:
                    tc_id = getattr(tc, "id", "")
                    tool_calls.append({
                        "id": tc_id,
                        "name": getattr(tc, "name", ""),
                        "arguments": getattr(tc, "args", {}),
                        "output": tool_outputs.get(tc_id),
                    })
        
        # Generate a message ID based on content hash (for consistency)
        msg_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{instance_id}:{role}:{content[:100]}"))
        
        result.append({
            "message_id": msg_id,
            "type": msg_type,
            "role": role,
            "content": content,
            "thinking": thinking,
            "thinking_extracted": thinking_extracted,
            "tool_calls": tool_calls,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    
    return result
