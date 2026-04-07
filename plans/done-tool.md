# Plan: `done()` Tool with Same-Turn Pattern

## Goal

Detect when a child agent has finished its task using a `done()` tool call in the **same response turn** as its final output.

---

## Changes

### 1. Auto-inject `done()` instruction into system prompt

**File:** `daemon/loader.py`

Inject into every agent's system prompt:
```
When your task is complete, call `done()` in the SAME response turn as your final output.
```

### 2. Add `done` tool (no params)

**File:** `daemon/tools/__init__.py`

```python
def done() -> dict:
    """Task complete."""
    return {"status": "done"}
```

Register in tools dict: `"done": done`

### 3. Add post-tools check for `done()`

**File:** `daemon/graph.py`

Add a second conditional edge from `tools` node:

```python
def should_end(state: MessagesState) -> str:
    """Check if done() was called after tools executed."""
    messages = state["messages"]
    # Look at the last AIMessage with tool_calls (before ToolMessage)
    for msg in reversed(messages[:-1]):
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            if any(tc.get("name") == "done" for tc in msg.tool_calls):
                return END
    return "agent"  # continue looping
```

```python
graph.add_conditional_edges("tools", should_end, {"agent": "agent", END: END})
```

**Why:** `should_continue` runs before tools. `should_end` runs after tools execute. This ensures tools run first, then we check for `done()`.

---

## Behavior

| Scenario | Flow |
|----------|------|
| `done()` + other tools | Tools execute → `should_end` returns END |
| Only other tools | Tools execute → `should_end` returns agent |
| Text-only | `should_continue` returns End |

### Example: Successful completion

```
Turn N:
  LLM: [send_message("result"), done()]

  → should_continue sees tool_calls → "tools"
  → ToolNode executes send_message → parent receives "result"
  → ToolNode executes done()
  → should_end sees done() → END

  Parent received message before graph ended ✓
```

---

## Files to Modify

1. `daemon/loader.py` — Inject `done()` instruction
2. `daemon/tools/__init__.py` — Add `done` tool
3. `daemon/graph.py` — Add `should_end` conditional edge

---

## Note

Response validation change (step 4 of old plan) removed — it's unnecessary. `_is_empty_content` already returns False when tool_calls exist.
