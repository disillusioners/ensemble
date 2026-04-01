# Phase 3: Graph Integration

## Objective

Wire the `ContextCompactor` into the graph execution pipeline by adding a pre-invocation compaction step in `manager.py`'s `_process_message_with_tracking()` and `send_message()` methods. Uses `graph.aupdate_state()` with `RemoveMessage` pattern to replace messages in-place. Includes compaction dedup (WARN-2), retry-skip guard (WARN-5), and system prompt token budget integration.

## Context

- **Previous phase**: Phase 2 — Compaction Engine (completed)
- **Key files**: `daemon/compaction.py` with `ContextCompactor`, `daemon/manager.py` for integration
- **Key decisions**:
  - Compaction runs **before** `graph.astream()` in `_process_message_with_tracking()`
  - Uses `RemoveMessage` sentinels via `graph.aupdate_state()` — verified to work correctly
  - System prompt tokens are fetched from the `PromptCache` (already computed per agent)
  - One `ContextCompactor` instance per `SessionManager` (shared across sessions, config-driven)
  - Compaction is **skipped on retry** (`is_retry=True`) — state was already compacted (WARN-5)
  - Compaction is **skipped if recently compacted** — dedup via `SessionState.compacted_at` (REV-CRIT-1: custom state schema)
  - **REV-CRIT-1**: `MessagesState` has no `compacted_at` channel — must use custom `SessionState(MessagesState)` with `compacted_at: Optional[str] = None` field so dedup metadata persists in checkpoints

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create `SessionState(MessagesState)` custom state class (REV-CRIT-1)** | Extend `MessagesState` with `compacted_at: Optional[str] = None` field. This is critical — without it, `aupdate_state({"compacted_at": ...})` is silently dropped because `MessagesState` has no such channel. | `daemon/graph.py` |
| 2 | **Update graph builder to use `SessionState`** | Replace `MessagesState` with `SessionState` in the `StateGraph()` constructor. All existing message handling works identically (inherits from `MessagesState`). | `daemon/graph.py` |
| 3 | **Create `ContextCompactor` instance on `SessionManager`** | Initialize in `SessionManager.__init__()` using compaction config + LLM config. Store as `self._compactor`. Add check for `compaction.enabled`. | `daemon/manager.py` |
| 4 | **Add `_maybe_compact_context()` async method** | New method on `SessionManager` that: (a) gets current state via `graph.aget_state()`, (b) builds `CompactionContext` (including `last_compacted_at` from `state["compacted_at"]`), (c) calls `self._compactor.compact_state()`, (d) if result, calls `graph.aupdate_state()` with replacement messages AND `compacted_at` | `daemon/manager.py` |
| 5 | **Wire compaction into `_process_message_with_tracking()` with retry guard** | Call `await self._maybe_compact_context(session_id, graph, config)` right before `graph.astream()`. **Skip if `is_retry=True`** — retry resumes from checkpoint, state was already compacted. | `daemon/manager.py` |
| 6 | **Add `_get_system_prompt_tokens()` helper** | Small helper that resolves the agent_id for a session and gets cached prompt token count from `self.prompt_cache` | `daemon/manager.py` |
| 7 | **Store `compacted_at` in state via `SessionState`** | After successful compaction, store timestamp via `await graph.aupdate_state(config, {"compacted_at": iso_timestamp})`. This NOW works because `SessionState` has the `compacted_at` field (REV-CRIT-1). | `daemon/manager.py` |
| 8 | **Add compaction to `send_message()` path** | The non-streaming `send_message()` also needs compaction (uses `graph.ainvoke()`). Same pattern: compact before invoke, no retry guard needed here. | `daemon/manager.py` |

## Key Files

- `daemon/graph.py` — **MODIFY** — Replace `MessagesState` with `SessionState` in `StateGraph()` constructor
- `daemon/manager.py` — Main integration point, ~120 lines of new code
- `daemon/compaction.py` — Existing compactor (no changes expected)

## Detailed Design

### `SessionState` Custom Schema (REV-CRIT-1)

**Problem**: `compacted_at` stored via `aupdate_state({"compacted_at": ...})` is silently dropped because `MessagesState` has no such channel. The dedup mechanism is completely non-functional without this fix.

**Fix**: Create a custom state class extending `MessagesState`:

```python
# In daemon/graph.py (or daemon/state.py if preferred)
from typing import Optional, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.graph import MessagesState

class SessionState(MessagesState):
    """Extended state schema for agent sessions.
    
    Inherits all message handling from MessagesState (add_messages reducer).
    Adds compaction metadata fields that persist in checkpoints.
    """
    # Compaction dedup: ISO timestamp of last successful compaction
    # Stored/retrieved via graph.aupdate_state() and state.values["compacted_at"]
    compacted_at: Optional[str] = None
```

**Graph builder update** — replace `MessagesState` with `SessionState`:

```python
# BEFORE (broken for compacted_at):
# graph = StateGraph(MessagesState)

# AFTER (REV-CRIT-1 fix):
graph = StateGraph(SessionState)
```

**Why this works**: LangGraph's `StateGraph` schema defines which channels exist. When you call `aupdate_state({"compacted_at": "..."})`, LangGraph checks if the key exists in the schema. With `MessagesState`, there's no `compacted_at` key → silently dropped. With `SessionState`, the key exists → value is stored in the checkpoint and retrievable via `state.values["compacted_at"]`.

**Reading `compacted_at`** in compaction:

```python
# In _maybe_compact_context():
state = await graph.aget_state(config)
last_compacted_at = state.values.get("compacted_at")  # Now works!
```

**Writing `compacted_at`** after compaction:

```python
# After replacement messages:
await graph.aupdate_state(
    config,
    {"messages": result.replacement_messages},
    as_node="agent",
)
# Store dedup marker (NOW works because SessionState has this field):
if result.compacted_at:
    await graph.aupdate_state(
        config,
        {"compacted_at": result.compacted_at},
        as_node="agent",
    )
```

### `_maybe_compact_context()` Implementation

```python
async def _maybe_compact_context(
    self,
    session_id: str,
    graph: CompiledStateGraph,
    config: dict,
) -> None:
    """Check if context compaction is needed and perform it.
    
    This runs before each graph.astream() / graph.ainvoke() call.
    If compaction is performed, the graph state is updated in-place
    via aupdate_state() using RemoveMessage sentinels, which also
    creates a checkpoint.
    
    Args:
        session_id: The session ID.
        graph: The compiled graph instance.
        config: The LangGraph config dict with thread_id.
    """
    # Skip if compaction is disabled
    if not self.config.compaction.enabled:
        return
    
    try:
        # 1. Get current state from checkpointer
        state = await graph.aget_state(config)
        if not state or not state.values:
            return
        
        messages = state.values.get("messages", [])
        if not messages:
            return
        
        # 2. Get system prompt token count for this session's agent
        system_prompt_tokens = self._get_system_prompt_tokens(session_id)
        
        # 3. Get last compaction timestamp for dedup (WARN-2, REV-CRIT-1)
        last_compacted_at = state.values.get("compacted_at")
        
        # 4. Build compaction context
        context = CompactionContext(
            messages=messages,
            system_prompt_tokens=system_prompt_tokens,
            model_name=self.config.llm.model,
            config=self.config.compaction,
            llm_config={
                "base_url": self.config.llm.base_url,
                "api_key": self.config.llm.api_key,
                "model": self.config.llm.model,
                "temperature": self.config.llm.temperature,
                "request_timeout": self.config.llm.request_timeout,
            },
            last_compacted_at=last_compacted_at,
        )
        
        # 5. Run compaction
        result = await self._compactor.compact_state(context)
        
        if result is None or result.replacement_messages is None:
            return  # No compaction needed
        
        # 6. Update graph state with replacement messages using RemoveMessage pattern
        await graph.aupdate_state(
            config,
            {"messages": result.replacement_messages},
            as_node="agent",
        )
        
        # 7. Store compaction timestamp in state for dedup (REV-CRIT-1: works via SessionState)
        if result.compacted_at:
            await graph.aupdate_state(
                config,
                {"compacted_at": result.compacted_at},
                as_node="agent",
            )
        
        # 8. Log compaction metrics
        logger.info(
            f"[Compaction] Session {session_id[:8]}...: "
            f"{result.compaction_type} | "
            f"messages: {result.messages_before} → {result.messages_after} | "
            f"tokens: {result.tokens_before} → {result.tokens_after} "
            f"(saved {result.tokens_saved})"
            + (f" | warning: {result.summarization_error}" if result.summarization_error else "")
        )
        
    except Exception as e:
        # Compaction failure should NEVER block message processing
        logger.warning(f"[Compaction] Failed for session {session_id[:8]}...: {e}")
```

### Integration Point in `_process_message_with_tracking()` with Retry Guard (WARN-5)

The compaction call goes **right before** the `graph.astream()` call, but is **skipped on retry**:

```python
# In _process_message_with_tracking(), around line 1119:

# --- Context Compaction (NEW) ---
# Skip compaction on retry — state was already compacted before first attempt
if not is_retry:
    await self._maybe_compact_context(session_id, graph, config)
# --- End Context Compaction ---

# Build input - on retry with checkpoint, resume from None
if is_retry:
    if await self._has_checkpoint(session_id):
        logger.info(f"Resuming session {session_id[:8]}... from checkpoint")
        graph_input = None  # LangGraph will resume from checkpoint
    else:
        logger.warning(f"Retry for session {session_id[:8]}... but no checkpoint found")
        graph_input = {"messages": [message]}
else:
    # First attempt - add message to conversation
    graph_input = {"messages": [message]}

# Stream through graph execution
try:
    async for event in graph.astream(graph_input, config, stream_mode=["updates", "messages"]):
        # ... existing streaming logic unchanged ...
```

**Why skip on retry?**
- On first attempt, compaction runs and creates a checkpoint with compacted state
- On retry, the graph resumes from that compacted checkpoint
- Running compaction again would be redundant and could cause issues
- The `is_retry` flag is already available in the method signature

### Integration in `send_message()` (Non-Streaming Path)

The `send_message()` method at line ~577 also invokes the graph. Add compaction:

```python
async def send_message(self, session_id: str, message: str) -> MessageResult:
    graph = self.get_session(session_id)
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": self.config.limits.graph_recursion_limit,
    }
    
    # --- Context Compaction (NEW) ---
    await self._maybe_compact_context(session_id, graph, config)
    # --- End Context Compaction ---
    
    result = await graph.ainvoke({"messages": [message]}, config)
    # ... existing processing unchanged ...
```

### `_get_system_prompt_tokens()` Helper

```python
def _get_system_prompt_tokens(self, session_id: str) -> int:
    """Get the cached system prompt token count for a session's agent.
    
    Falls back to computing it if not cached.
    """
    meta = self._session_repository.get(session_id)
    if meta is None:
        return 0
    
    cached = self.prompt_cache.get(meta.agent_id)
    if cached is not None:
        return cached[1]  # (prompt, token_count) tuple
    
    # Not cached — compute it
    from daemon.loader import load_and_cache_prompt
    from pathlib import Path
    agent_path = Path(meta.agent_dir)
    _, tokens = load_and_cache_prompt(meta.agent_id, agent_path, self.prompt_cache)
    return tokens
```

### `_get_last_compacted_at()` — Now reads from `SessionState` (REV-CRIT-1)

The `_get_last_compacted_at()` helper is **no longer needed** as a separate method. Since `SessionState` has `compacted_at` as a proper field, reading it is simply:

```python
last_compacted_at = state.values.get("compacted_at")
```

This replaces the old approach of reading from `state.metadata.get("compacted_at")` which was broken because metadata is not the right place for state values.

### SessionManager `__init__()` Addition

```python
# In SessionManager.__init__():
# Initialize context compactor
if self.config.compaction.enabled:
    self._compactor = ContextCompactor(
        config=self.config.compaction,
        llm_config={
            "base_url": self.config.llm.base_url,
            "api_key": self.config.llm.api_key,
            "model": self.config.llm.model,
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
```

## Constraints

- **`SessionState` must be used everywhere `MessagesState` was used** — graph builder, state reads, state writes (REV-CRIT-1)
- `compacted_at` is read from `state.values["compacted_at"]`, NOT from `state.metadata`
- Compaction must NEVER raise exceptions that propagate to the caller — wrap in try/except and log
- `graph.aupdate_state()` with `as_node="agent"` is critical for LangGraph to correctly attribute the state change
- The `_maybe_compact_context()` must work with both streaming (`astream`) and non-streaming (`ainvoke`) paths
- System prompt tokens MUST be included in the total calculation (system prompt can be 5K+ tokens)
- Compaction must not interfere with the retry mechanism — **skip on retry** (WARN-5)
- Compaction dedup via `SessionState.compacted_at` must prevent re-compaction on every subsequent message (WARN-2)
- `RemoveMessage` sentinels in replacement messages must have valid IDs (messages from checkpoint always have IDs)

## Deliverables

- [ ] `SessionState(MessagesState)` custom state class with `compacted_at` field in `daemon/graph.py` (REV-CRIT-1)
- [ ] Graph builder updated to use `SessionState` instead of `MessagesState` (REV-CRIT-1)
- [ ] `ContextCompactor` instance created in `SessionManager.__init__()`
- [ ] `_maybe_compact_context()` method implemented with `RemoveMessage` pattern
- [ ] `_get_system_prompt_tokens()` helper implemented
- [ ] `compacted_at` read from `state.values["compacted_at"]` (not metadata) (REV-CRIT-1)
- [ ] Compaction wired into `_process_message_with_tracking()` with `if not is_retry:` guard
- [ ] Compaction wired into `send_message()` (non-streaming path)
- [ ] Compaction `compacted_at` stored in `SessionState` via `aupdate_state()` for dedup
- [ ] Compaction metrics logged with `[Compaction]` prefix
- [ ] Error handling: compaction failure doesn't block message processing
