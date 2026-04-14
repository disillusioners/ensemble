# Proposed Architecture

## 3 Event Types Only (plus keepalive)

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{instance_id}` | Client connects |
| `checkpoint` | `{instance_id, messages[], checkpoint_id}` | After each LangGraph node completes |
| `error` | `{error, details}` | Unrecoverable failure |
| `keepalive` | `{}` | Every 30s timeout |

> **Cancellation handling**: User-initiated cancellation maps to `error` event: `{"error": "cancelled"}`. No separate event type needed.

**Removed entirely**: `content_chunk`, `thinking`, `tool_call`, `tool_complete`, `message_received`, `message_completed`, `processing_started`, `processing_completed`, `processing_failed`, `child_completed`, `child_failed`, `instance_completed`, `title_updated`, `cancelled`, `message_queued`, `completed`, `status_changed`

> **Title updates**: Frontend polls via instance API for title changes. No SSE event needed.

> **Instance completion**: Final checkpoint includes `completed: true` flag. No separate event needed.

---

## Message Format (Identical in SSE and REST API)

```json
{
  "id": "msg-uuid-from-langgraph",
  "role": "assistant",
  "content": "Hello!",
  "thinking": null,
  "thinking_extracted": null,
  "tool_calls": null,
  "created_at": "2026-04-13T15:30:34.050055+00:00"
}
```

---

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  LangGraph astream(stream_mode=["updates"])                     │
│                                                                  │
│  agent node completes ──► emit checkpoint event                 │
│  tools node completes ──► emit checkpoint event                 │
│  agent node completes ──► emit checkpoint event                 │
│  ...                                                             │
│                                                                  │
│  Each checkpoint event contains ALL messages from state,        │
│  with LangGraph's msg.id as the message identity.               │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Frontend                                                       │
│                                                                  │
│  On "checkpoint" event:                                         │
│    this.messages.set(normalize(event.messages))                │
│                                                                  │
│  On SSE disconnect/error:                                       │
│    this.isStreaming.set(false)                                  │
│                                                                  │
│  No delta merging, no message_id tracking, no accumulation.    │
│  Just replace the list.                                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. LangGraph's `msg.id` is Source of Truth

No more `compute_message_id()`. The ID assigned by LangGraph is the single source of truth for message identity.

### 2. Full State Replacement

Instead of delta updates, each checkpoint sends the complete message list. Frontend simply replaces its list.

### 3. Tool Output Embedding

Tool outputs are embedded in the parent `AIMessage.tool_calls[].output` field, not as separate events.

### 4. Stable ID Fallback

For messages without `msg.id`, a deterministic hash of `(role, content[:200], tool_call_id)` prevents duplicates on re-emission.
