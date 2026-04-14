# Phase 6: Frontend SSE Service — Full Rewrite

---

## Goal

Rewrite `sse.service.ts` to handle only 4 event types: `connected`, `checkpoint`, `error`, `keepalive`.

---

## New Implementation

### Signals to Keep

```typescript
isStreaming = signal(false);
events = signal<SSEEvent[]>([]);  // Keep for debugging
latestError = signal<...>(null);   // Keep
```

### Signals to Remove

```typescript
statusUpdates = signal<Map<string, string>>(new Map());   // DELETE
titleUpdates = signal<...>(null);                          // DELETE
messageDeltas = signal<MessageDelta[]>([]);                // DELETE — replaced by checkpoint
```

### New Signal

```typescript
messages = signal<Message[]>([]);  // Replaces messageDeltas
```

### New Simplified Event Handling

```typescript
private handleEvent(event: MessageEvent) {
  const data = JSON.parse(event.data);
  
  switch (event.type) {
    case 'connected':
      this.events.update(e => [...e, { type: 'connected', data }]);
      break;
      
    case 'checkpoint':
      this.isStreaming.set(true);
      this.messages.set(
        data.messages.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content || '',
          thinking: m.thinking || null,
          thinking_extracted: m.thinking_extracted || null,
          tool_calls: m.tool_calls || null,
          created_at: m.created_at || new Date().toISOString(),
        }))
      );
      break;
      
    case 'error':
      this.isStreaming.set(false);  // ← FIXED: was missing
      this.latestError.set(data);
      break;
      
    case 'keepalive':
      break;
  }
}

// Handle disconnect/error — set isStreaming to false
private handleClose() {
  this.isStreaming.set(false);
}
```

> **FIXED**: `isStreaming` must be set to `false` on SSE `onerror`/`onclose`. The original draft only set it to `true` on checkpoint but never reset it.

---

## Methods to DELETE

| Lines | Method | Why |
|-------|--------|-----|
| 29-52 | `isValidInstanceEvent()`, `emitDelta()` | No more deltas |
| 70-472 | `connectInternal()` (all event listeners) | Replaced by single handler |
| 474-508 | `handleCompletedEvent()` | No more completion events |

---

## Verification

```bash
# Verify no more delta handling
grep -rn "content_chunk\|thinking\|tool_call\|emitDelta" frontend/src/app/services/sse.service.ts

# Verify isStreaming reset on error
grep -rn "isStreaming.set(false)" frontend/src/app/services/sse.service.ts

# Build to verify
cd frontend && npm run build
```
