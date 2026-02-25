# Session Retry Problem and Fix Proposal

## 1. Problem Description

Each session does NOT work in a dedicated thread - it uses **queue-based async processing** with two levels of retry:

1. **LLM-level retry (inside LangGraph)**: Built into the LangGraph agent for handling transient LLM API failures
2. **Queue-level retry (outside LangGraph)**: Implemented in the queue handler for handling session-level failures

This dual retry mechanism creates complexity and potential issues when failures occur.

## 2. Duplicate Execution Issue

When a tool fails in a multi-task job, the following problems arise:

### 2.1 Queue-Level Retry Re-sends Same Message

When the queue-level retry triggers, it re-sends the **same message** to the session, causing:
- Duplicate execution of already-completed steps
- Potential side effects (e.g., files created twice, commands run twice)
- Conversation history pollution with duplicate messages

### 2.2 Race Condition

There is a potential race condition between:
- **Queue handler**: Processing the retry
- **Watchdog**: Monitoring for hung sessions

This race condition could cause:
- Double retries (both queue and watchdog retry simultaneously)
- Conflicting state between queue and graph execution
- Unpredictable behavior when multiple retry mechanisms trigger

### 2.3 No Idempotency Protection

The current implementation lacks idempotency protection:
- No mechanism to detect if a task has already been processed
- No checkpoint-based resume capability
- Messages are blindly re-executed on retry

## 3. Proposed Solution: LangGraph Checkpointing for Resume

Instead of restarting execution on retry, we should use LangGraph's built-in checkpointing to **resume** from the last known good state:

### 3.1 Check if Checkpoint Exists Before Retry

```python
from langgraph.checkpoint.base import BaseCheckpointSaver

async def retry_with_checkpoint(session_id: str, checkpoint_id: str):
    """Check if checkpoint exists before attempting retry."""
    checkpoint_saver: BaseCheckpointSaver = get_checkpoint_saver()
    
    # Get the checkpoint metadata to verify it exists
    checkpoint = await checkpoint_saver.get(checkpoint_id)
    
    if checkpoint is None:
        raise ValueError(f"No checkpoint found for id: {checkpoint_id}")
    
    return checkpoint
```

### 3.2 Use graph.invoke(None, config) to Resume from Checkpoint

```python
from langgraph.graph import StateGraph
from langgraph.checkpoint.config import RunnableConfig

async def resume_from_checkpoint(
    graph: StateGraph,
    session_id: str,
    checkpoint_id: str
):
    """Resume execution from a checkpoint instead of restarting."""
    
    config = RunnableConfig(
        configurable={
            "thread_id": session_id,
            "checkpoint_id": checkpoint_id
        }
    )
    
    # Resume from checkpoint - no input needed, uses saved state
    result = await graph.ainvoke(None, config)
    
    return result
```

### 3.3 Avoid Adding Duplicate Messages

To prevent duplicate messages in conversation history:

```python
async def safe_retry(session_id: str, task_message: str):
    """Retry with idempotency check."""
    
    # Check if this task was already processed
    session = await get_session(session_id)
    
    # Check last few messages for duplicate
    recent_messages = await session.get_recent_messages(n=5)
    is_duplicate = any(
        msg.content == task_message and msg.metadata.get("processed")
        for msg in recent_messages
    )
    
    if is_duplicate:
        logger.info(f"Task already processed, skipping duplicate: {task_message}")
        return
    
    # Process the task normally
    await process_task(session_id, task_message)
```

## 4. Implementation Hints

### 4.1 Configure Checkpointing in LangGraph

```python
from langgraph.checkpoint.memory import MemorySaver

# Create graph with checkpointing
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.set_entry_point("agent")

# Use MemorySaver for development (use PostgresSaver for production)
checkpointer = MemorySaver()

# Compile with checkpointer
graph_with_checkpoints = graph.compile(checkpointer=checkpointer)
```

### 4.2 Store Checkpoint ID with Session

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class SessionState:
    session_id: str
    checkpoint_id: Optional[str] = None
    last_status: str = "pending"
    
    def update_checkpoint(self, checkpoint_id: str):
        self.checkpoint_id = checkpoint_id
        self.last_status = "checkpointed"
```

### 4.3 Retry Logic with Checkpoint Resume

```python
async def handle_tool_failure(session_id: str, error: Exception):
    """Handle tool failure with checkpoint-based retry."""
    
    session = await get_session(session_id)
    
    # Get the last checkpoint ID
    checkpoint_id = session.checkpoint_id
    
    if checkpoint_id is None:
        # No checkpoint - must restart (fallback behavior)
        logger.warning(f"No checkpoint for session {session_id}, restarting")
        await restart_session(session_id)
        return
    
    # Resume from checkpoint
    try:
        result = await graph_with_checkpoints.ainvoke(
            None,  # No new input
            config={
                "configurable": {
                    "thread_id": session_id,
                    "checkpoint_id": checkpoint_id
                }
            }
        )
        
        # Update checkpoint ID for next retry
        session.checkpoint_id = result.get("checkpoint_id")
        await session.save()
        
    except Exception as e:
        logger.error(f"Failed to resume from checkpoint: {e}")
        await restart_session(session_id)
```

### 4.4 Idempotency Key for Tasks

```python
import uuid
from hashlib import sha256

def generate_task_id(content: str) -> str:
    """Generate deterministic ID for task deduplication."""
    return sha256(content.encode()).hexdigest()[:16]

async def submit_task(session_id: str, content: str):
    """Submit task with idempotency check."""
    
    task_id = generate_task_id(content)
    
    # Check if already processed
    if await is_task_processed(task_id):
        logger.info(f"Task {task_id} already processed")
        return
    
    # Submit with task ID for tracking
    await queue.enqueue({
        "session_id": session_id,
        "content": content,
        "task_id": task_id
    })
```

## 5. Benefits of Checkpoint-Based Retry

| Aspect | Current (Restart) | Proposed (Checkpoint Resume) |
|--------|-------------------|------------------------------|
| **Duplicate Execution** | High risk | No risk |
| **State Preservation** | Lost | Preserved |
| **Message History** | Polluted | Clean |
| **Idempotency** | None | Full support |
| **Race Condition** | Possible | Eliminated |

## 6. Migration Path

1. **Phase 1**: Add checkpointing to LangGraph compilation
2. **Phase 2**: Modify queue handler to check for existing checkpoints before retry
3. **Phase 3**: Implement idempotency key tracking for tasks
4. **Phase 4**: Remove queue-level retry in favor of checkpoint resume
5. **Phase 5**: Add monitoring for checkpoint-based recovery metrics
