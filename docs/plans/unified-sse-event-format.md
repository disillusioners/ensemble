# Plan: Unified SSE Event Format

## Context

The current SSE event system has two major issues:

1. **Streaming events use `id: 0`** instead of auto-increment IDs, breaking SSE reconnection with `Last-Event-ID`
2. **SSE events and API messages have different formats**, forcing the frontend to handle multiple schemas

### Current State Analysis

| Component | Format Produced |
|-----------|----------------|
| `persistence.py:get_instance_messages()` | `{message_id, type, role, content, thinking, thinking_extracted, tool_calls, created_at}` |
| `format_sse_event()` in `api.py` | `{message_id, instance_id, ...data}` |
| `broadcast_streaming_event()` in `event_bus.py` | `{instance_id, event_type, data: {chunk/id/name/arguments/output}}` |
| `UnifiedMessage.to_sse_data()` | Same as API but with `instance_id` added |
| `UnifiedMessage.to_api_response()` | Different shape — missing `instance_id`, `source` |

The `UnifiedMessage` model exists but is barely used, creating three independent serialization paths.

### Root Causes

1. **`id=0` for streaming events** (`api.py:973`): `event_id = event.get("event_id", 0)` — streaming events don't carry an ID
2. **No unified envelope**: `format_sse_event()` wraps data inconsistently
3. **Duplicate events**: `message_completed` AND `processing_completed` both emitted with overlapping content

---

## Design Goals

1. **Single message format** — SSE events and GET /messages API return identical shapes
2. **Monotonic event IDs** — streaming events get real IDs, not `0`
3. **Clear event semantics** — each event type has a defined purpose
4. **Frontend simplification** — `messages: Observable<Message[]>` as single source of truth

---

## New Architecture

### Unified Message Format

Every message-bearing SSE event will emit a `message` object matching GET /messages:

```typescript
// Typescript interface (frontend/models/index.ts)
interface Message {
  message_id: string;
  instance_id?: string;  // Optional: set by SSE, not by API
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[];
  source?: string | null;  // api, telegram:xxx, child:instance_id
  created_at: string;
}
```

### New SSE Event Envelope

All SSE events will use this envelope:

```typescript
interface SSEEvent {
  id: string;           // Monotonic ID (for Last-Event-ID)
  event: string;        // Event type (message_received, content_chunk, etc.)
  data: {
    // Common fields
    instance_id: string;
    message_id?: string;  // Queue message UUID (same across related events)
    
    // For message-bearing events (message_received, message_completed)
    message?: Message;    // Full message object
    
    // For streaming events (content_chunk, thinking, tool_call, tool_complete)
    delta?: {
      type: 'chunk' | 'thinking' | 'tool_call' | 'tool_complete';
      content?: string;   // For chunk/thinking
      tool_call?: ToolCall;  // For tool_call/tool_complete
      index: number;      // Per-type monotonic counter, resets per message_id
    };
    
    // For lifecycle events (processing_started, processing_completed, error)
    status?: {
      success?: boolean;
      error?: string;
      stage?: string;      // For errors: where the error occurred
      message_id?: string; // For errors: which message failed
      metadata?: Record<string, unknown>;
    };
  };
}
```

### Event Types and Their Payloads

| Event Type | Purpose | `message` | `delta` | `status` |
|------------|---------|----------|---------|----------|
| `message_received` | User message queued | ✅ Full message | ❌ | ❌ |
| `content_chunk` | Token streaming | ❌ | ✅ `{type:'chunk', content, index}` | ❌ |
| `thinking` | Thinking content | ❌ | ✅ `{type:'thinking', content, index}` | ❌ |
| `tool_call` | Tool invocation | ❌ | ✅ `{type:'tool_call', tool_call, index}` | ❌ |
| `tool_complete` | Tool finished | ❌ | ✅ `{type:'tool_complete', tool_call, index}` | ❌ |
| `message_completed` | Final canonical message | ✅ Full message | ❌ | ❌ |
| `processing_started` | Task begun | ❌ | ❌ | ✅ `{metadata}` |
| `processing_completed` | Task done | ❌ | ❌ | ✅ `{success}` |
| `error` | Error occurred | ❌ | ❌ | ✅ `{error}` |

### Delta Index Semantics

The `delta.index` field follows these rules:
- **Per-type monotonic**: Each `delta.type` has its own counter
- **Per-message reset**: Counter resets when a new `message_id` processing cycle begins
- **Zero-based**: First event for each type starts at index 0
- **No gaps**: Indices are sequential within a type for a given message

**Example sequence for one message:**
```
delta { type: "chunk",     content: "Hello",  index: 0 }
delta { type: "chunk",     content: " world", index: 1 }
delta { type: "thinking",  content: "User..", index: 0 }  # resets per type
delta { type: "chunk",     content: "!",      index: 2 }
delta { type: "tool_call", tool_call: {...},  index: 0 }
```

### Key Changes

1. **Drop duplicate**: `processing_completed` no longer contains `content`, `thinking`, `tool_calls` — use `message_completed` instead
2. **Prefixed streaming IDs**: `broadcast_streaming_event()` generates IDs with `s` prefix (`s1`, `s2`) to avoid collision with DB auto-increment IDs
3. **Unified serialization**: Single `to_dict()` method in `UnifiedMessage`
4. **API alignment**: `persistence.py:get_instance_messages()` returns same shape as SSE
5. **Streaming counter cleanup**: `_streaming_counters` cleaned up when instance ends

---

## Impact Assessment

### ResponseDispatcher Impact

The `EventBus.subscribe_all()` broadcasts to global subscribers including `ResponseDispatcher` (manages source sessions like Telegram, Scheduler).

**Required updates:**
- `ResponseDispatcher` receives the new delta envelope format
- It must handle `{type, content, tool_call, index}` within the `delta` object
- No functional change needed — it already extracts relevant fields, just needs to navigate new structure

### Global Subscriber Impact

All subscribers via `EventBus.subscribe_all()` receive the new envelope. Verify these consumers:
- `ResponseDispatcher` (Telegram, Scheduler sessions)
- Any SSE clients connected via `/instances/{id}/events`

---

## Implementation Steps

### Phase 1: Backend Core (Models & Event Bus)

#### Step 1.1: Update `message_models.py`

**File**: `daemon/message_models.py`

**Changes**:
1. Add `SSEEventPayload` model — canonical SSE envelope
2. Add `SSEEventDelta` model — streaming delta metadata
3. Add `SSEEventStatus` model — lifecycle event metadata
4. Replace `to_sse_data()` and `to_api_response()` with single `to_dict(include_nulls: bool = False)`
5. Add `instance_id` to API response format

```python
# New models to add
class SSEEventPayload(BaseModel):
    """Canonical SSE event envelope."""
    event_type: str
    instance_id: str
    message_id: str | None = None
    message: dict[str, Any] | None = None  # Full message object
    delta: dict[str, Any] | None = None    # Streaming delta
    status: dict[str, Any] | None = None   # Lifecycle status

class SSEEventDelta(BaseModel):
    """Streaming delta metadata."""
    type: str  # 'chunk' | 'thinking' | 'tool_call' | 'tool_complete'
    content: str | None = None
    tool_call: dict[str, Any] | None = None
    index: int = 0

class SSEEventStatus(BaseModel):
    """Lifecycle event status."""
    success: bool | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None

# Update UnifiedMessage
class UnifiedMessage(BaseModel):
    # ... existing fields ...
    
    def to_dict(self, include_nulls: bool = False) -> dict[str, Any]:
        """Single serialization for both API and SSE."""
        result = {
            "message_id": self.message_id,
            "instance_id": self.instance_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }
        # Optional fields
        optional = {
            "thinking": self.thinking,
            "thinking_extracted": self.thinking_extracted,
            "tool_calls": [tc.model_dump() for tc in self.tool_calls] if self.tool_calls else None,
            "source": self.source,
        }
        for key, value in optional.items():
            if include_nulls or value is not None:
                result[key] = value
        return result
```

#### Step 1.2: Update `event_bus.py`

**File**: `daemon/services/event_bus.py`

**Changes**:
1. Add streaming counter per instance: `_streaming_counters: dict[str, int]`
2. Add `_next_streaming_id(instance_id)` method
3. Update `broadcast_streaming_event()` signature to accept delta metadata
4. Include `event_id` in streaming event dict

```python
# In __init__:
self._streaming_counters: dict[str, int] = {}

def _next_streaming_id(self, instance_id: str) -> str:
    """Generate monotonic streaming event ID per instance with 's' prefix.
    
    Uses 's' prefix to avoid collision with DB auto-increment IDs.
    SSE client can distinguish: 's5' = streaming, '42' = DB event.
    """
    if instance_id not in self._streaming_counters:
        self._streaming_counters[instance_id] = 0
    self._streaming_counters[instance_id] += 1
    return f"s{self._streaming_counters[instance_id]}"

def cleanup_instance(self, instance_id: str) -> None:
    """Clean up instance resources including streaming counters."""
    # ... existing cleanup (queues, subscribers) ...
    self._streaming_counters.pop(instance_id, None)  # NEW: prevent memory leak

# Update broadcast_streaming_event
async def broadcast_streaming_event(
    self,
    instance_id: str,
    event_type: str,
    message_id: str,
    delta: dict[str, Any],
) -> None:
    # ... existing queue logic ...
    event = {
        "instance_id": instance_id,
        "event_type": event_type,
        "event_id": self._next_streaming_id(instance_id),  # NEW: real ID
        "message_id": message_id,
        "data": delta,
    }
    # ...
```

### Phase 2: Backend API & Service Layer

#### Step 2.1: Update `api.py`

**File**: `daemon/api.py`

**Changes**:
1. Rewrite `format_sse_event()` to produce unified envelope
2. Ensure all event types produce consistent `data` shape
3. Update SSE endpoint to use new envelope

```python
def format_sse_event(event) -> dict:
    """Format event for SSE response with unified envelope."""
    
    # Determine event type and ID
    if hasattr(event, 'kind'):  # DB Event model
        event_type = event.kind
        event_id = event.id
        data = json.loads(event.data) if event.data else {}
        instance_id = event.instance_id
        message_id = event.message_id
    elif isinstance(event, dict):  # Streaming event
        event_type = event.get("event_type", "unknown")
        event_id = event.get("event_id", 0)
        data = event.get("data", {})
        instance_id = event.get("instance_id", "")
        message_id = event.get("message_id")
    else:
        return {"event": "error", "data": json.dumps({"error": "Unknown event type"})}
    
    # Build unified envelope
    envelope = {
        "instance_id": instance_id,
        "message_id": message_id,
    }
    
    # Add message, delta, or status based on event type
    if event_type in ("message_received", "message_completed"):
        envelope["message"] = data.get("message") or data
    elif event_type in STREAMING_EVENT_TYPES:
        envelope["delta"] = data
    else:
        envelope["status"] = data
    
    return {
        "id": str(event_id),
        "event": event_type,
        "data": json.dumps(envelope),
    }
```

#### Step 2.2: Update `message_service.py`

**File**: `daemon/services/message_service.py`

**Changes**:
1. Update `on_assistant_message_completed()` to emit `message` directly (not wrapped)
2. Remove duplicate content from `processing_completed` — keep only `{success, assistant_message_id}`

```python
# on_assistant_message_completed changes
await self._event_bus.create_event(
    instance_id=instance_id,
    kind=EventKind.MESSAGE_COMPLETED,
    message_id=original_message_id,
    data=message.to_dict(),  # Direct message, not wrapped
)

# processing_completed: lightweight status only
await self._event_bus.create_processing_completed_event(
    instance_id=instance_id,
    message_id=original_message_id,
    result={
        "success": True,
        "assistant_message_id": assistant_message_id,
    },
)
```

### Phase 3: Backend Streaming & Persistence

#### Step 3.1: Update `manager.py`

**File**: `daemon/manager.py`

**Changes**:
1. Update all `broadcast_streaming_event()` calls to use new signature
2. Include `delta` structure with `type`, `content`/`tool_call`, `index`
3. Update line ~1172-1380 (streaming event broadcasts)

```python
# Example: content_chunk
chunk_index = self._get_next_chunk_index(instance_id)  # Add counter
await self._event_bus.broadcast_streaming_event(
    instance_id=instance_id,
    event_type="content_chunk",
    message_id=message_id,
    delta={
        "type": "chunk",
        "content": content_buffer,
        "index": chunk_index,
    }
)

# Example: tool_call
tool_index = self._get_next_tool_index(instance_id)  # Add counter
await self._event_bus.broadcast_streaming_event(
    instance_id=instance_id,
    event_type="tool_call",
    message_id=message_id,
    delta={
        "type": "tool_call",
        "tool_call": {
            "id": tc_id,
            "name": tc_name,
            "arguments": tc_args,
        },
        "index": tool_index,
    }
)
```

#### Step 3.2: Update `persistence.py`

**File**: `daemon/persistence.py`

**Changes**:
1. `get_instance_messages()` returns same format as `UnifiedMessage.to_dict()`
2. Add `instance_id` field (for SSE use)
3. Remove `type` field (LangGraph internal detail)

```python
# Update result dict (around line 183)
result.append({
    "message_id": msg_id,
    "instance_id": instance_id,  # NEW
    "role": role,
    "content": content,
    "thinking": thinking,
    "thinking_extracted": thinking_extracted,
    "tool_calls": tool_calls,
    "created_at": created_at,
    # NOTE: type field removed (LangGraph internal)
})
```

### Phase 4: Frontend Updates

#### Step 4.1: Update `frontend/src/app/models/index.ts`

**Changes**:
1. Update `Message` interface to match backend
2. Add `SSEEventEnvelope` interface
3. Simplify `MessageDelta` to reference `Message`

```typescript
// Updated Message interface
export interface Message {
  message_id: string;
  instance_id?: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  thinking?: string | null;
  thinking_extracted?: string | null;
  tool_calls?: ToolCall[];
  source?: string | null;
  created_at: string;
}

// New SSE envelope interface
export interface SSEEventEnvelope {
  instance_id: string;
  message_id?: string;
  message?: Message;
  delta?: {
    type: 'chunk' | 'thinking' | 'tool_call' | 'tool_complete';
    content?: string;
    tool_call?: ToolCall;
    index: number;
  };
  status?: {
    success?: boolean;
    error?: string;
    metadata?: Record<string, unknown>;
  };
}

// Simplified MessageDelta
export interface MessageDelta {
  type: MessageDeltaType;
  instance_id: string;
  message_id: string;
  content?: string;
  tool_call?: ToolCall;
  message?: Message;
  success?: boolean;
  error?: string;
  timestamp: string;
}
```

#### Step 4.2: Update `frontend/src/app/services/sse.service.ts`

**Changes**:
1. Simplify event handlers to extract from unified envelope
2. Use `message` directly from envelope for `message_received`, `message_completed`
3. Use `delta` for streaming events

```typescript
// Example: content_chunk handler simplified
eventSource.addEventListener('content_chunk', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const envelope: SSEEventEnvelope = JSON.parse(e.data);
      
      this.events.update(prev => [...prev, {
        event_id: parseInt(e.lastEventId || '0'),
        type: 'content_chunk',
        instance_id: envelope.instance_id,
        message_id: envelope.message_id,
        data: envelope,
      }]);
      
      if (envelope.delta) {
        this.emitDelta({
          type: 'content_chunk',
          instance_id: envelope.instance_id,
          message_id: envelope.message_id,
          content: envelope.delta.content,
        });
      }
    } catch (err) {
      console.error('[SSE] Failed to parse content_chunk:', err);
    }
  });
});

// Example: message_received handler
eventSource.addEventListener('message_received', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const envelope: SSEEventEnvelope = JSON.parse(e.data);
      
      this.emitDelta({
        type: 'message_received',
        instance_id: envelope.instance_id,
        message_id: envelope.message_id,
        message: envelope.message,  // Direct message from envelope
      });
    } catch (err) {
      console.error('[SSE] Failed to parse message_received:', err);
    }
  });
});
```

### Phase 5: Testing & Verification

#### Step 5.1: Backend Tests

**File**: `tests/unit/test_sse_unified.py` (new)

Test cases:
- `test_unified_message_to_dict` — serialization parity
- `test_sse_event_envelope` — all event types produce valid envelope
- `test_streaming_event_ids` — monotonic IDs per instance
- `test_api_message_format` — GET /messages returns same shape

#### Step 5.2: Integration Test

**File**: `tests/integration/test_sse_events.py`

Test:
- Send message via API
- Collect all SSE events
- Verify `message_received` → streaming events → `message_completed` chain
- Verify final messages match GET /messages API

---

## File Changes Summary

| File | Changes | Effort | Notes |
|------|---------|:------:|-------|
| `daemon/message_models.py` | Add SSE envelope models, unify `to_dict()` | Small | |
| `daemon/services/event_bus.py` | Add streaming counter with prefixed IDs, cleanup | Medium | New `_streaming_counters.pop()` in cleanup |
| `daemon/api.py` | Rewrite `format_sse_event()`, SSE endpoint | Medium | Add `legacy_compat` flag support |
| `daemon/services/message_service.py` | Remove duplicate content from `processing_completed` | Small | After frontend Phase B |
| `daemon/manager.py` | Update streaming calls (~20 locations) | **Large** | Multiple call sites for content_chunk, thinking, tool_call, tool_complete |
| `daemon/persistence.py` | Align output with `UnifiedMessage.to_dict()` | Small | |
| `frontend/src/app/models/index.ts` | Update interfaces | Small | |
| `frontend/src/app/services/sse.service.ts` | Simplify handlers + legacy compat | **Large** | Union type narrowing + dual format handling |
| `tests/unit/test_sse_unified.py` | New test file | Small | |
| `tests/integration/test_sse_events.py` | New integration test | Small | |

---

## Backward Compatibility

### Critical: Migration Phase Ordering

**The following phase ordering MUST be enforced:**

```
Phase A: Backend emits BOTH formats (legacy_compat=true)
         └─ SSE includes both new envelope AND flat fields

Phase B: Frontend updated to use new envelope format
         └─ Frontend handles both formats during this transition

Phase C: Backend sets legacy_compat=false
         └─ SSE only includes new envelope (AFTER Phase B complete)
```

**Why this matters:**
- If backend drops `content` from `processing_completed` before frontend handles `message_completed`, message display breaks
- The `legacy_compat` flag ensures frontend can consume either format during transition

### Breaking Changes

The following changes are **intentional breaking changes**:

1. `content_chunk` data: `{chunk: "..."}` → `{delta: {type: "chunk", content: "...", index: N}}`
2. `message_completed` data: `{original_message_id, message: {...}}` → `{message: {...}}`
3. `processing_completed` data: no longer contains `content`, `thinking`, `tool_calls`

### Compatibility Shim (Optional)

If needed during migration, add a flag to `format_sse_event()`:

```python
def format_sse_event(event, legacy_compat: bool = False) -> dict:
    """Format event for SSE response."""
    # ... new format logic ...
    
    if legacy_compat:
        # Add flat fields alongside new envelope
        envelope["message_id"] = message_id
        envelope["instance_id"] = instance_id
        envelope.update(data)  # Merge original data
    
    return {
        "id": str(event_id),
        "event": event_type,
        "data": json.dumps(envelope),
    }
```

---

## Expected Benefits

1. **Single source of truth**: Frontend can use `messages: Observable<Message[]>` from both initial load (API) and updates (SSE)
2. **Better debugging**: All events follow same envelope structure
3. **SSE resumability**: Streaming events now have real IDs for `Last-Event-ID` reconnection
4. **Less code**: Remove duplicate serialization paths and event handlers
5. **Clearer semantics**: Each event type has a defined purpose and payload

---

## Verification

After implementation:

1. Run `pytest tests/ -v` — all tests pass
2. Start dev server: `./dev.sh`
3. Send test message via API
4. Compare SSE events with GET /messages API response
5. Verify frontend chat component works correctly
6. Test SSE reconnection with `Last-Event-ID` header
7. Verify streaming IDs are prefixed with `s` and don't collide with DB IDs

### Required Test Cases

```python
@pytest.mark.asyncio
async def test_streaming_ids_gapless_per_type():
    """Streaming IDs must be sequential per delta type and per message_id."""
    # Verify s1, s2, s3... without gaps within each type
    # Verify counter resets for new message_id

@pytest.mark.asyncio
async def test_streaming_ids_disjoint_from_db():
    """Streaming IDs must not collide with DB event IDs."""
    # DB events: 1, 2, 3...
    # Streaming: s1, s2, s3...
    # No collision possible

@pytest.mark.asyncio
async def test_legacy_compat_flag():
    """Backend respects legacy_compat flag."""
    # With legacy_compat=true: envelope includes both new + flat fields
    # With legacy_compat=false: envelope only includes new format
```
