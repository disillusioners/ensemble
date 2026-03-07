# SSE Refactor Plan: Signals → Hybrid (Observables + Signals)

## Problem
Current architecture uses Signals for SSE event streams, requiring awkward "reset pattern" to re-trigger effects.

**Symptom:** `latestCompletedMessage.set(null)` needed after every consumption to allow effect to re-run.

**Root Cause:** SSE is an event stream, not state. Signals are designed for synchronous state, not event streams.

## Solution
Refactor to **Hybrid Architecture**:
- **Observables** for SSE event streams (semantically correct, RxJS operators)
- **Signals** for UI state (fine-grained reactivity, template binding)

## Migration Steps

### Phase 1: Update SseService
**File:** `frontend/src/app/services/sse.service.ts`

**Changes:**
1. Replace event signals with Observables:
   - `latestCompletedMessage` → `onMessageCompleted$` (Subject)
   - `latestError` → `onError$` (Subject)
   - `titleUpdates` → `onTitleUpdated$` (Subject)

2. Keep state signals:
   - `isStreaming` ✓ (UI state)
   - `partialMessages` ✓ (streaming progress)
   - `statusUpdates` ✓ (status tracking)

3. EventSource handlers emit to Subjects instead of setting signals

### Phase 2: Update ChatComponent
**File:** `frontend/src/app/pages/chat/chat.component.ts`

**Changes:**
1. Replace `effect()` with `subscribe()` for events:
   ```typescript
   // Before (effect with reset)
   effect(() => {
     const msg = this.sseService.latestCompletedMessage();
     if (msg) {
       this.messages.update(prev => [...prev, msg]);
       this.sseService.latestCompletedMessage.set(null); // ❌ Reset pattern
     }
   });
   
   // After (subscribe, no reset)
   this.sseService.onMessageCompleted$
     .pipe(takeUntil(this.destroy$))
     .subscribe(msg => {
       this.messages.update(prev => [...prev, msg]);
       this.isSending.set(false);
     });
   ```

2. Keep signals for UI state:
   - `messages` ✓
   - `isSending` ✓
   - `sendError` ✓
   - `pendingMessage` ✓

3. Add proper cleanup with `takeUntil(this.destroy$)`

### Phase 3: Testing
1. Send multiple messages in sequence
2. Verify no freeze after responses
3. Check that loading indicator appears/disappears correctly
4. Test error handling
5. Verify title updates work

## Files to Modify

1. **`frontend/src/app/services/sse.service.ts`**
   - Convert event signals to Observables
   - Keep UI state signals
   - Update EventSource handlers

2. **`frontend/src/app/pages/chat/chat.component.ts`**
   - Replace effects with subscriptions
   - Add cleanup logic
   - Remove reset pattern

## Expected Outcome

✅ No more reset pattern needed
✅ Events flow naturally through Observables
✅ UI state managed with Signals
✅ Better semantics (events as streams, state as signals)
✅ Access to RxJS operators for event transformation
✅ Aligned with Angular best practices

## Architecture Diagram

```
EventSource (SSE)
      ↓
[Observables] Event streams
  - onMessageCompleted$
  - onError$
  - onTitleUpdated$
  - onContentChunk$
      ↓
[RxJS Operators] Transformations
  - filter, map, debounce, etc.
      ↓
[Subscribe] Bridge to state
      ↓
[Signals] UI State
  - messages
  - isSending
  - isStreaming
  - partialMessages
      ↓
[Template] Reactive binding
```

## Timeline
- **Step 1:** Refactor SseService (~10 min)
- **Step 2:** Refactor ChatComponent (~10 min)
- **Step 3:** Testing (~5 min)
- **Total:** ~25 minutes
