# Phase 1: Backend Core — Serialization & Persistence

> **⚠️ Phase 1 changes message ID format**: `compute_message_id()` (deterministic hash) is replaced
> with LangGraph's native `msg.id` (UUIDs). The REST API response field `message_id` stays the same,
> but VALUES change. Verify no external systems or frontend logic depends on the old ID format.

---

## Goals

1. Add `_stable_message_id()` and `serialize_message()` to `daemon/utils.py`
2. Rewrite `get_instance_messages()` in `persistence.py` to use LangGraph `msg.id`
3. Remove `compute_message_id()` function entirely

---

## 1. `daemon/utils.py` — Add Serialization Helpers

### 1.1 Add `_stable_message_id()` helper

```python
import hashlib


def _stable_message_id(msg) -> str:
    """Generate a stable ID for messages without msg.id.
    
    Uses a hash of role + content + tool_call_id so the same message
    always gets the same ID across re-emissions. This prevents duplicates
    when LangGraph re-emits the same message in a checkpoint.
    
    Args:
        msg: LangChain BaseMessage with potentially no .id attribute.
    
    Returns:
        A deterministic 16-char hex string prefixed with "fallback-".
    """
    role = msg.type if hasattr(msg, 'type') else str(msg.__class__.__name__)
    content = getattr(msg, 'content', '') or ''
    content_str = content if isinstance(content, str) else str(content)
    tc_id = getattr(msg, 'tool_call_id', '') or ''
    
    key = f"{role}:{content_str[:200]}:{tc_id}"
    digest = hashlib.md5(key.encode('utf-8', errors='replace')).hexdigest()[:16]
    return f"fallback-{digest}"
```

### 1.2 Add `serialize_message()` function

```python
def serialize_message(msg, tool_outputs: dict | None = None) -> dict:
    """Serialize a LangChain message to dict matching REST API format.
    
    Must handle all 5 thinking extraction paths:
      1. additional_kwargs.get("reasoning_content")
      2. additional_kwargs.get("thinking")  
      3. msg.reasoning_content attribute
      4. msg.thinking attribute (Claude models)
      5. msg.content as list with type="reasoning" blocks
    
    Args:
        msg: LangChain BaseMessage (HumanMessage, AIMessage, ToolMessage, etc.)
        tool_outputs: Optional map of tool_call_id -> output content.
    
    Returns:
        Dict with message_id, role, content, thinking, tool_calls, created_at.
        Note: msg.id=None uses _stable_message_id() (hash-based) for deterministic
        fallback, not random UUIDs — prevents duplicate messages on re-emission.
    """
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    role = role_map.get(msg.type, msg.type)
    content = getattr(msg, 'content', '') or ''
    
    # Thinking extraction (5 paths)
    thinking = None
    if hasattr(msg, 'additional_kwargs'):
        kwargs = msg.additional_kwargs or {}
        thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
    if not thinking and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
        thinking = msg.reasoning_content
    if not thinking and hasattr(msg, 'thinking') and msg.thinking:
        thinking = msg.thinking
    if not thinking and isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "reasoning":
                    thinking = block.get("reasoning") or block.get("summary_text", "")
                    break
    
    # Parse <think/> tags from content
    content_str = content if isinstance(content, str) else str(content)
    content_str, thinking_extracted = parse_think_tags(content_str)
    
    # Tool calls for AIMessage
    tool_calls = None
    if hasattr(msg, 'tool_calls') and msg.tool_calls:
        tool_outputs = tool_outputs or {}
        tool_calls = []
        for tc in msg.tool_calls:
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
    
    return {
        # LangGraph msg.id mapped to message_id (external API field name).
        # msg.id=None uses _stable_message_id() for deterministic fallback.
        "message_id": getattr(msg, 'id', None) or _stable_message_id(msg),
        "role": role,
        "content": content_str,
        "thinking": thinking,
        "thinking_extracted": thinking_extracted,
        "tool_calls": tool_calls,
        "created_at": None,  # Filled from checkpoint timestamps in persistence.py
    }
```

---

### 1.3 Add `get_next_sequence()` helper

```python
# Add to daemon/utils.py
_sequence_counter: dict[str, int] = {}

def get_next_sequence(instance_id: str) -> int:
    """Get next monotonically incrementing sequence number for an instance.
    
    Args:
        instance_id: The instance to get sequence for.
    
    Returns:
        The next sequence number (starts at 1).
    """
    current = _sequence_counter.get(instance_id, 0)
    next_seq = current + 1
    _sequence_counter[instance_id] = next_seq
    return next_seq
```

### 1.4 Update `serialize_message()` docstring

The docstring should clearly reference the 5 thinking paths documented above in section 1.2.

---

## 2. `daemon/persistence.py` — Rewrite `get_instance_messages()`

**Location**: Lines 74-212

### 2.1 Rewrite to use LangGraph IDs

```python
async def get_instance_messages(
    checkpointer: AsyncSqliteSaver,
    instance_id: str,
) -> list[dict[str, Any]]:
    """Get message history from LangGraph checkpoints using native msg.id."""
    from daemon.utils import serialize_message
    
    config = {"configurable": {"thread_id": instance_id}}
    state = await checkpointer.aget(config)
    if not state:
        return []
    
    messages = state.get("channel_values", {}).get("messages", [])
    if not messages:
        return []
    
    # Collect timestamps from checkpoint history
    msg_timestamps = await _collect_timestamps(checkpointer, config, messages)
    
    # Build tool_outputs map from ToolMessages
    tool_outputs = {}
    for msg in messages:
        if hasattr(msg, 'tool_call_id'):
            tool_outputs[msg.tool_call_id] = msg.content
    
    result = []
    for msg in messages:
        if msg.type == "tool":
            continue  # ToolMessages included in AIMessage's tool_calls
        
        serialized = serialize_message(msg, tool_outputs)
        serialized["instance_id"] = instance_id
        serialized["created_at"] = msg_timestamps.get(msg.id)
        result.append(serialized)
    
    return result
```

### 2.2 Remove `compute_message_id()` function

Delete lines 23-37 (`compute_message_id` function).

### 2.3 Keep unchanged

- `get_checkpointer()` (lines 40-71)
- `_collect_timestamps()` helper (extract from current `get_instance_messages()`)

---

## 3. Remove `compute_message_id()` Usage Everywhere

| File | Line | Current | New |
|------|------|---------|-----|
| `persistence.py` | 23-37 | `def compute_message_id(...)` | Delete function |
| `persistence.py` | ~193 | `compute_message_id(instance_id, role, content)` | Use `msg.id` directly |

---

## Verification

```bash
# Verify no more compute_message_id references
grep -rn "compute_message_id" daemon/ --include="*.py"

# Verify serialize_message is importable
python -c "from daemon.utils import serialize_message; print('OK')"

# Verify get_instance_messages works
# (run after LangGraph stream format verification in Phase 3.5)
```
