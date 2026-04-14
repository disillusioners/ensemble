# Phase 5: Frontend Models — Cleanup

> **Note**: No rename needed. JSON API and frontend keep `message_id` (semantically clear).
> `serialize_message()` maps LangGraph's `msg.id` → `message_id` internally.

---

## Goals

1. Delete SSE-specific types (no longer needed)
2. Add simplified SSE event types
3. Keep `message_id` unchanged in interfaces

---

## 1. Delete SSE-Specific Types

| What | Why |
|------|-----|
| `EventType` union (14 types) | Only `connected`, `checkpoint`, `error`, `keepalive` |
| `SSEEventEnvelope` | No more envelopes |
| `SSEDelta` | No more deltas |
| `SSEStatus` | No more status events |
| `MessageDeltaType` union (9 types) | No more deltas |
| `CanonicalMessage` | No more canonical messages |
| `MessageDelta` | No more deltas |

---

## 2. Add Simplified SSE Types

```typescript
type SseEventType = 'connected' | 'checkpoint' | 'error' | 'keepalive';

interface SSEEvent {
  type: SseEventType;
  data: Record<string, unknown>;
}
```

---

## 3. Keep Unchanged

| What | Why |
|------|-----|
| `Message.message_id` | Keeps `message_id` — semantically clear |
| `MessageResponse.message_id` | Keeps `message_id` — matches API |
| `ToolCall` | Unchanged |
| `MessageCreate` | Unchanged |
| Agent types | Unchanged |
| Source types | Unchanged |

---

## 4. Keep `message_id` — No Rename

The frontend and JSON API continue to use `message_id`:

```typescript
interface Message {
  message_id: string;  // ← Keep this (maps to LangGraph msg.id internally)
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[] | null;
  created_at?: string;
}
```

---

## Verification

```bash
# Verify message_id is still in models
grep -n "message_id" frontend/src/app/models/index.ts

# Verify no SSE-specific types remain
grep -n "SSEDelta\|CanonicalMessage\|MessageDelta\|EventType" frontend/src/app/models/index.ts

# Build to verify compilation
cd frontend && npm run build
```
