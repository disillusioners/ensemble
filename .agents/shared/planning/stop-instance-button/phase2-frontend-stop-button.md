# Phase 2: Frontend — Stop Button

## Objective
Replace the send button with a visually distinct stop button when the instance is actively streaming/processing. Wire up the stop button to call the backend stop endpoint and handle the resulting `cancelled` SSE event.

## Coupling
- **Depends on**: Phase 1 (backend stop endpoint)
- **Coupling type**: loose — only depends on the API contract (`POST /instances/{id}/stop`)
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: Consumes `POST /api/instances/{id}/stop`
- **Why this coupling**: Frontend needs the endpoint to exist, but can be coded in parallel once the contract is agreed

## Context
- The `MessageInputComponent` currently shows a send button that is disabled when `isSending()` (from parent) is true
- The `ChatComponent` tracks `isSending` signal and `SseService.isStreaming` signal
- The SSE service already handles `completed`, `error`, `content_chunk` events but NOT `cancelled`
- The backend broadcasts a `cancelled` SSE event when an operation is cancelled
- The project uses `@Input()` decorators for component props (not Angular 17.1+ signal `input()` function)

**Important note on signal vs `@Input()`**: The parent passes `isSending()` (signal call) to get the current value. The child receives it as a plain boolean via `@Input()`. Therefore, the template must use `isStreaming` (no parentheses) — it is a plain boolean, not a signal.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `stopInstance()` to API service | New method: `POST /instances/{id}/stop` → `{ stopped: bool, cancelled_requests: number }` | `frontend/src/app/services/api.service.ts` |
| 2 | Add `cancelled` event handler to SSE service | Listen for `cancelled` events, set `isStreaming.set(false)`, clear partial messages | `frontend/src/app/services/sse.service.ts` |
| 3 | Add stop button to message-input component | Add `@Input() isStreaming = false` (plain boolean, not signal). Add `@Output() stopRequested`. Conditionally show stop icon button instead of send icon when streaming — use `@if (isStreaming)` in template (no parentheses). | `frontend/src/app/components/message-input/message-input.component.ts` + `.html` |
| 4 | Style the stop button | Red/orange background, square-stop SVG icon, same sizing as send button | `frontend/src/app/components/message-input/message-input.scss` |
| 5 | Wire stop button in chat component | Add `onStopInstance()` method, pass `[isStreaming]="isSending()"` to message-input, handle `(stopRequested)` event, handle cancelled SSE event | `frontend/src/app/pages/chat/chat.component.ts` + `.html` |

## Key Files
- `frontend/src/app/services/api.service.ts` — Add `stopInstance()` method
- `frontend/src/app/services/sse.service.ts` — Add `cancelled` event listener
- `frontend/src/app/components/message-input/message-input.component.ts` — Add stop button inputs/outputs
- `frontend/src/app/components/message-input/message-input.html` — Conditional button rendering (`@if (isStreaming)` — no parentheses)
- `frontend/src/app/components/message-input/message-input.scss` — Stop button styling
- `frontend/src/app/pages/chat/chat.component.ts` — Wire stop handler
- `frontend/src/app/pages/chat/chat.html` — Pass `[isStreaming]="isSending()"`

## Implementation Details

### Task 1: API Service

```typescript
// In api.service.ts, after deleteInstance method (~line 72)
stopInstance(instanceId: string): Observable<{ stopped: boolean; cancelled_requests: number }> {
  return this.http.post<{ stopped: boolean; cancelled_requests: number }>(
    `${this.API_BASE}/instances/${instanceId}/stop`, {}
  );
}
```

### Task 2: SSE Cancelled Event

In `sse.service.ts`, add inside `connectInternal()` after the `error` event listener (~line 342):

```typescript
eventSource.addEventListener('cancelled', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      if (!this.isValidInstanceEvent(data)) return;
      console.log('[SSE] Received cancelled event:', data.message_id, 'reason:', data.reason);

      const event: SSEEvent = {
        event_id: parseInt(e.lastEventId || '0'),
        type: 'cancelled',
        instance_id: data.instance_id,
        message_id: data.message_id,
        data: data,
      };
      this.events.update(prev => [...prev, event]);

      // Clear partial message on cancellation
      if (data.message_id) {
        this.partialMessages.update(prev => {
          const updated = new Map(prev);
          updated.delete(data.message_id);
          return updated;
        });
      }

      // Reset streaming state
      this.isStreaming.set(false);

      if (data.message_id) {
        this.statusUpdates.update(prev => new Map(prev).set(data.message_id, 'cancelled'));
      }
    } catch (err) {
      console.error('Failed to parse cancelled event:', err);
    }
  });
});
```

### Task 3: Message Input Component — TypeScript

```typescript
// In message-input.component.ts — add after existing @Input() declarations
@Input() isStreaming = false;  // Plain boolean @Input() — template uses isStreaming (no parentheses)
@Output() stopRequested = new EventEmitter<void>();

onStopRequested(): void {
  this.stopRequested.emit();
}
```

### Task 3b: Message Input Component — Template

```html
<!-- message-input.html -->
<form class="input-container" (submit)="$event.preventDefault()">
  <div class="input-wrapper">
    <textarea
      #textarea
      [value]="message()"
      (input)="onInput($event)"
      (keydown.enter)="handleSubmit(); $event.preventDefault()"
      [disabled]="disabled"
      placeholder="Type your message..."
      rows="1"
      class="input-textarea"
      [class.active]="canSend"
    ></textarea>
    
    <!-- Stop button: shown when streaming. Note: no parentheses on isStreaming — it's a plain @Input(), not a signal. -->
    @if (isStreaming) {
      <button
        type="button"
        class="stop-button"
        (click)="onStopRequested()"
      >
        <svg class="stop-icon" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="1" />
        </svg>
      </button>
    } @else {
      <button
        type="button"
        [disabled]="!canSend"
        class="send-button"
        [style.backgroundColor]="canSend ? color : '#343541'"
        [style.color]="canSend ? 'white' : '#6e6e80'"
        [style.cursor]="canSend ? 'pointer' : 'not-allowed'"
        (click)="handleSubmit()"
      >
        <svg class="send-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
        </svg>
      </button>
    }
  </div>
  
  <p class="input-hint">
    Press <kbd class="kbd">Enter</kbd> to send, 
    <kbd class="kbd">Shift + Enter</kbd> for new line
  </p>
</form>
```

### Task 4: Stop Button Styles

Add to `message-input.scss`:

```scss
.stop-button {
  padding: 0.75rem;
  border-radius: 0.625rem;
  border: none;
  background-color: #ef4444; // Red-500
  color: white;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;

  &:hover {
    background-color: #dc2626; // Red-600
    transform: scale(1.05);
  }

  &:active {
    background-color: #b91c1c; // Red-700
    transform: scale(0.95);
  }
}

.stop-icon {
  width: 1.25rem;
  height: 1.25rem;
}
```

### Task 5: Chat Component Wiring

In `chat.component.ts`, add the stop handler:

```typescript
protected onStopInstance(): void {
  const instance = this.currentInstance();
  if (!instance) return;

  this.api.stopInstance(instance.instance_id).subscribe({
    next: (response) => {
      console.log('[Chat] Stop result:', response);
      this.isSending.set(false);
      this.pendingMessage.set(null);
    },
    error: (err) => {
      console.error('[Chat] Failed to stop instance:', err);
      // Still reset state — the SSE cancelled event may handle it
      this.isSending.set(false);
      this.pendingMessage.set(null);
    }
  });
}
```

Also add an effect to handle the `cancelled` SSE event resetting `isSending` (in the constructor effects section, after the error effect ~line 174):

```typescript
// Effect to handle cancelled events - reset state when instance processing is cancelled
effect(() => {
  const events = this.sseService.events();
  const lastEvent = events[events.length - 1];
  const currentInstance = this.currentInstance();
  if (lastEvent?.type === 'cancelled' && lastEvent.instance_id === currentInstance?.instance_id) {
    console.log('[Chat] Cancelled event received, resetting state');
    this.isSending.set(false);
    this.pendingMessage.set(null);
  }
}, { allowSignalWrites: true });
```

In `chat.html`, update the message-input binding (~line 112):

```html
<app-message-input
  (sendMessage)="onSendMessage($event)"
  (stopRequested)="onStopInstance()"
  [disabled]="isSending()"
  [isStreaming]="isSending()"
  [agentColor]="instanceAgent()?.id || 'coder'"
></app-message-input>
```

> **Why `[isStreaming]="isSending()"`**: The parent passes `isSending()` (signal call) to get the current value. The child receives it as a plain boolean via `@Input() isStreaming = false`. The send button becomes disabled anyway via `[disabled]="isSending()"` — the stop button is the primary affordance during streaming.

## Constraints
- Stop button must be same size/position as send button for visual consistency
- Template must use `isStreaming` (no parentheses) in `@if` — it is a plain `@Input()`, not a signal
- The textarea remains intentionally disabled during streaming (already handled by `[disabled]="disabled"` from parent)
- Must handle edge case: stop pressed right as processing completes naturally (both paths converge to idle)
- Must work with existing fallback effect (streaming stopped → isSending reset)
- Don't break existing send flow when not streaming

## Deliverables
- [ ] Stop button appears when instance is sending/streaming
- [ ] Stop button is visually distinct (red color, square stop icon)
- [ ] Clicking stop calls `POST /instances/{id}/stop` and resets UI state
- [ ] `cancelled` SSE event is handled and resets streaming state
- [ ] Partial messages are cleaned up after cancellation
- [ ] Send button reappears after stop completes
- [ ] Instance accepts new messages after being stopped
- [ ] No regression in normal send/receive flow
