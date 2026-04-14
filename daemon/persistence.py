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
    from typing import cast
    from langgraph.checkpoint.base import CheckpointTuple
    
    from daemon.utils import serialize_message
    
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
    
    # Collect all checkpoints with timestamps
    # We need to iterate oldest-to-newest to track when messages first appeared
    checkpoints_data: list[tuple[str | None, list[Any]]] = []
    
    async for checkpoint_tuple in checkpointer.alist(config, limit=1000):
        ct = cast(CheckpointTuple, checkpoint_tuple)
        checkpoint = ct.checkpoint
        if not isinstance(checkpoint, dict):
            continue
        ts = checkpoint.get("ts")
        checkpoint_messages = checkpoint.get("channel_values", {}).get("messages", [])
        checkpoints_data.append((ts, checkpoint_messages))
    
    # Reverse to get oldest-to-newest order
    checkpoints_data.reverse()
    
    # Track when each message first appeared
    msg_timestamps: dict[str, str] = {}
    for ts, checkpoint_messages in checkpoints_data:
        if not ts:
            continue
        for msg in checkpoint_messages:
            msg_id = getattr(msg, 'id', None)
            if msg_id and msg_id not in msg_timestamps:
                msg_timestamps[msg_id] = ts
    
    # Build a map of tool_call_id -> output from ToolMessages
    tool_outputs = {}
    for msg in messages:
        if hasattr(msg, 'tool_call_id'):  # It's a ToolMessage
            tool_outputs[msg.tool_call_id] = msg.content
    
    result = []
    
    for msg in messages:
        msg_type = getattr(msg, 'type', 'unknown')
        
        # Skip tool messages (they're included in tool_calls of AIMessages)
        if msg_type == 'tool':
            continue
        
        # Serialize the message using shared utility
        serialized = serialize_message(msg, tool_outputs)
        
        # Get message ID and use it to look up timestamp
        msg_id = serialized["message_id"]
        created_at = msg_timestamps.get(msg_id)
        if not created_at:
            created_at = state.get("ts")
        
        # Add instance_id and created_at
        serialized["instance_id"] = instance_id
        serialized["created_at"] = created_at
        
        result.append(serialized)
    
    return result
