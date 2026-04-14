# Proposed Architecture

## 3 Event Types Only (plus keepalive)

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{instance_id}` | Client connects |
| `checkpoint` | `{instance_id, messages[], checkpoint_id, checkpoint_sequence}` | After each LangGraph node completes |
| `error` | `{error, details}` | Unrecoverable failure |
| `keepalive` | `{}` | Every 30s timeout |

> **Sequence numbers**: Each checkpoint includes `checkpoint_sequence` — a monotonically incrementing integer per instance. This ensures correct ordering when checkpoints arrive out of order (e.g., due to network reordering or reconnection mid-stream).

> **Cancellation handling**: User-initiated cancellation maps to `error` event: `{"error": "cancelled"}`. No separate event type needed.

**Removed entirely**: `content_chunk`, `thinking`, `tool_call`, `tool_complete`, `message_received`, `message_completed`, `processing_started`, `processing_completed`, `processing_failed`, `child_completed`, `child_failed`, `instance_completed`, `title_updated`, `cancelled`, `message_queued`, `completed`, `status_changed`

> **Title updates**: Frontend polls via instance API for title changes. No SSE event needed.

> **Instance completion**: Final checkpoint includes `completed: true` flag. No separate event needed.

---

## Unified Message Format

> **Note**: JSON API and frontend use `message_id` (semantically clear). This maps to LangGraph's internal `msg.id`. No rename needed.

```json
{
  "message_id": "msg-uuid-from-langgraph",
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
│  with LangGraph's msg.id mapped to `message_id`.                 │
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

### 1. LangGraph's `msg.id` mapped to `message_id`

No more `compute_message_id()`. The ID assigned by LangGraph is the single source of truth, mapped to `message_id` in the JSON API. Frontend and REST API keep `message_id` (semantically clear).

### 2. Full State Replacement

Instead of delta updates, each checkpoint sends the complete message list. Frontend simply replaces its list.

### 3. Tool Output Embedding

Tool outputs are embedded in the parent `AIMessage.tool_calls[].output` field, not as separate events.

### 4. Stable ID Fallback

For messages without `msg.id`, a deterministic hash of `(role, content[:200], tool_call_id)` prevents duplicates on re-emission.
