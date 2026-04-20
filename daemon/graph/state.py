"""Session state schema for agent execution."""
from langgraph.graph import MessagesState
from typing import Optional


class SessionState(MessagesState):
    """Extended state schema for agent sessions.
    
    Inherits all message handling from MessagesState (add_messages reducer).
    Adds compaction metadata fields that persist in checkpoints.
    """
    # Compaction dedup: ISO timestamp of last successful compaction
    # Stored/retrieved via graph.aupdate_state() and state.values["compacted_at"]
    compacted_at: Optional[str] = None
