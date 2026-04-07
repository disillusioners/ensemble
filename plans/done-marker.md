# Plan: Structured Output with `</DONE>` Marker

## Goal

Detect when a child agent has finished its task using a `</DONE>` marker appended to the final message. The graph ends cleanly without relying on the LLM to make a tool call decision.

## Marker

```
</DONE>
```

- Appended by LLM at the end of the final message when task is complete
- Stripped before sending to parent agent
- Case-sensitive, no whitespace variations

---

## Changes

### 1. Auto-inject `</DONE>` instruction into system prompt

**File:** `daemon/loader.py` (or wherever prompts are assembled)

**Change:** When building the system prompt from agent MD files, inject the instruction at the end of the prompt:

```
[End of prompt assembled from MD files]

---
When your task is complete, append `</DONE>` at the end of your final message.
Do not include it in intermediate messages.
```

- Injected **once** per agent, not per turn
- Does NOT modify any `.md` files
- Language: simple, direct (not a system prompt lecture)

---

### 2. Modify `should_continue` to detect `</DONE>`

**File:** `daemon/graph.py`

**Change:**

```python
def should_continue(state: MessagesState) -> str:
    messages = state["messages"]
    last_message = messages[-1]
    
    # Check for tool calls first
    if getattr(last_message, 'tool_calls', None):
        return "tools"
    
    # Check for </DONE> marker in text response
    content = getattr(last_message, 'content', '') or ''
    if isinstance(content, list):
        # Handle content blocks (text + images)
        text_parts = [c['text'] for c in content if c.get('type') == 'text']
        content = ' '.join(text_parts)
    
    if '</DONE>' in content:
        return END
    
    return END  # No tool calls and no marker → also end (fallback)
```

**Note:** We no longer distinguish "text-only with content" from "empty response" — any text without tool calls ends the graph. The `</DONE>` marker is advisory, not a hard requirement.

---

### 3. Strip `</DONE>` before sending to parent

**File:** `daemon/manager.py` (or wherever messages are streamed to parent)

**Change:** When extracting the response message for the parent, remove the marker:

```python
def _strip_done_marker(content: str | list) -> str | list:
    if isinstance(content, list):
        # Handle content blocks
        return [
            {**c, 'text': c['text'].replace('\n</DONE>', '').replace('</DONE>', '')}
            if c.get('type') == 'text' else c
            for c in content
        ]
    return content.replace('\n</DONE>', '').replace('</DONE>', '')
```

Apply in the streaming response path before sending to parent.

---

### 4. Validation: `</DONE>` in empty response

**File:** `daemon/response_validation.py`

**Change:** Update validation to accept empty responses (let `should_continue` decide):

```python
# Remove or relax the empty content validation
# Empty content + no tool calls is now handled by should_continue
```

---

## Behavior Summary

| Scenario | Result |
|----------|--------|
| LLM sends tool calls | Execute tools, loop back |
| LLM sends text with `</DONE>` | Strip marker, send to parent, END |
| LLM sends text without `</DONE>` | Send to parent, END (fallback — same as current) |
| LLM sends empty (retry exhausted) | Graph stops |

---

## Files to Modify

1. `daemon/loader.py` — Inject `</DONE>` instruction into system prompt
2. `daemon/graph.py` — Update `should_continue` to check for marker
3. `daemon/manager.py` — Strip marker before sending to parent
4. `daemon/response_validation.py` — Relax empty content validation

## Files NOT Modified

- Any `.md` agent definition files
