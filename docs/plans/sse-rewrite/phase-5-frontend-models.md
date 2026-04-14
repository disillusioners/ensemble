# Phase 5: Frontend Models — Interface Updates

> **⚠️ CRITICAL**: This phase must ship with Phase 1 (backend `message_id` → `id` rename) in the **same PR/commit**.

---

## Goals

1. Update `Message` and `MessageResponse` interfaces: `message_id` → `id`
2. Delete SSE-specific types
3. Add simplified SSE event types

---

## 1. Update Interfaces

### Before:
```typescript
interface Message {
  message_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  // ...
}

interface MessageResponse {
  message_id: string;
  role: 'user' | 'assistant';
  content: string;
  // ...
}
```

### After:
```typescript
interface Message {
  id: string;                              // LangGraph's msg.id (was message_id)
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[] | null;
  created_at?: string;
}

interface MessageResponse {
  id: string;                              // was message_id — matches LangGraph
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  instance_id?: string;
}
```

---

## 2. Delete SSE-Specific Types

| Lines | What | Why |
|-------|------|-----|
| 91-106 | `EventType` union (14 types) | Only `connected`, `checkpoint`, `error`, `keepalive` |
| 108-114 | `SSEEventEnvelope` | No more envelopes |
| 117-123 | `SSEEventEnvelope` | No more envelopes |
| 125-130 | `SSEDelta` | No more deltas |
| 132-138 | `SSEStatus` | No more status events |
| 142-151 | `MessageDeltaType` union (9 types) | No more deltas |
| 154-164 | `CanonicalMessage` | No more canonical messages |
| 166-186 | `MessageDelta` | No more deltas |

---

## 3. Add Simplified SSE Types

```typescript
type SseEventType = 'connected' | 'checkpoint' | 'error' | 'keepalive';

interface SSEEvent {
  type: SseEventType;
  data: Record<string, unknown>;
}
```

---

## 4. Keep Unchanged

| Lines | What |
|-------|------|
| 2-13 | `InstanceStatus`, `InstanceInfo` |
| 15-21 | `InstanceListResponse` |
| 38-43 | `ToolCall` (unchanged) |
| 45-57 | `MessageCreate` |
| 67-76 | Agent types |
| 192-202 | Source types |

---

## 5. Find All References

```bash
grep -r "message_id" frontend/src --include="*.ts" -l
```

Expected to find files like:
- `chat.component.ts`
- `sse.service.ts`
- `models/index.ts`
- `chat-interface.component.ts`

---

## Verification

```bash
# Verify no more message_id references in models
grep -rn "message_id" frontend/src/app/models/index.ts

# Verify new id field is used
grep -rn "\.id" frontend/src/app/models/index.ts | grep -i message

# Build to verify compilation
cd frontend && npm run build
```
