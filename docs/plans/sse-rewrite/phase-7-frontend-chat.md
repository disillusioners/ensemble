# Phase 7: Frontend Chat Component — Simplification

---

## Goal

Remove all delta-processing effects and `message_id`-based lookups.

---

## Changes

### 1. Delete the main delta-processing effect (lines 80–349)

The 270-line effect that handles `processing_started`, `message_received`, `content_chunk`, `thinking`, `tool_call`, `tool_complete`, `processing_completed`, `message_completed` — **all deleted**.

### 2. Replace with simple checkpoint effect

```typescript
effect(() => {
  const messages = this.sseService.messages();
  if (messages.length > 0) {
    this.messages.set(messages.map(m => this.toViewModel(m)));
    this.isSending.set(false);
  }
});
```

### 3. Delete title update effect (lines 362–376)

Title updates come from the instance API (polling), not SSE.

### 4. Replace error handling effect

```typescript
effect(() => {
  const error = this.sseService.latestError();
  if (error) {
    this.isSending.set(false);
    // Show error in UI
  }
});
```

### 5. Evaluate fallback `isSending` reset effect (lines 352–359)

After the rewrite, `isSending` is reset when the first checkpoint arrives (Section 2). This may be redundant.

### 6. Delete `message_id`-based lookup logic (line 99)

No merging needed — `messages` signal is replaced entirely on each checkpoint.

### 7. Delete HTTP message merge logic (lines 511–528)

SSE messages ARE the source of truth. On connect, initial state comes from first checkpoint event. No merge needed.

### 8. Update `trackBy` function

```typescript
// Before (current)
trackBy(index: number, message: any): string {
  return message.message_id;
}

// After (stays message_id — backend sends message_id, not id)
trackBy(index: number, message: any): string {
  return message.message_id;
}
```

> **⚠️ IMPORTANT**: Phase 6's SSE service must map `m.message_id` (not `m.id`) from backend
> events. The backend sends `message_id` field. `m.id` does not exist and would cause
> `trackBy` to return `undefined`, breaking Angular `*ngFor` with full DOM re-renders.

---

## Verification

```bash
# Verify no more delta effects
grep -rn "messageDeltas\|statusUpdates\|titleUpdates" frontend/src/app/pages/chat/chat.component.ts

# Verify no more message_id references
grep -rn "message_id" frontend/src/app/pages/chat/chat.component.ts

# Verify trackBy uses id
grep -rn "trackBy" frontend/src/app/pages/chat/

# Build to verify
cd frontend && npm run build
```
