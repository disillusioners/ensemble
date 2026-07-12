# Phase 3: Frontend — SSE Handling, Pending Message UI, canInject Computed

## Objective
Handle the three new injection SSE events in the frontend, display pending injected messages with a distinct visual style in the chat UI, and modify the message input component to allow sending messages while an instance is RUNNING (injection mode) alongside the existing pause button.

**Critical (C6)**: Do NOT modify `isInstanceRunning()`. Add a separate `canInject` computed signal for RUNNING/WAITING_CHILDREN only. The Pause button visibility remains controlled by `isInstanceRunning()` (which includes QUEUED).

## Coupling
- **Depends on**: Phase 2
- **Coupling type**: loose
- **Shared files with other phases**: None (frontend-only)
- **Shared APIs/interfaces**: SSE event contract (event types + payload shape via `stream_message`) defined in Phase 2
- **Why this coupling**: Frontend depends only on the SSE event contract and query endpoint from Phase 2. No shared code files. Frontend development can start as soon as the contract is defined, even before Phase 2 backend is fully complete (using mocked events).

## Context
- **SseService** (`frontend/src/app/services/sse.service.ts`): Angular signal-based SSE handling. Currently handles 11 event types including `status_change`, `user_message`, `assistant_message`, `thinking`, `tool_call`, `tool_result`, `instance_created`, `context_usage`, `connected`, `error`, `keepalive`.
- **Message Input** (`frontend/src/app/components/message-input/`): 3-way toggle system. When instance is RUNNING/WAITING_CHILDREN/QUEUED → shows only Pause button. When PAUSED → shows Resume button. When IDLE/other → shows Send button.
- **Chat Interface** (`frontend/src/app/components/chat-interface/`): Displays messages with avatars and markdown rendering.
- **Chat Page** (`frontend/src/app/pages/chat/`): Main controller integrating message input and chat interface.

### Current Message Input State Logic (to be modified)

```
Status: RUNNING/WAITING_CHILDREN/QUEUED → Pause button only (NO text input)
Status: PAUSED → Resume button (with text input for resume message)
Status: IDLE/other → Send button (with text input)
```

### Target Message Input State Logic (C6)

```
Status: RUNNING/WAITING_CHILDREN → Text input + Send button (injection) + Pause button
    ├─ canInject() = true → shows text input + Send button
    └─ isInstanceRunning() = true → shows Pause button (UNCHANGED)
Status: QUEUED → Pause button only (canInject = false, isInstanceRunning = true)
Status: PAUSED → Resume button (UNCHANGED)
Status: IDLE/other → Send button (UNCHANGED)
```

**Key insight (C6)**: `isInstanceRunning()` is NOT modified. It still returns true for RUNNING + WAITING_CHILDREN + QUEUED, controlling Pause button visibility. A NEW `canInject` computed returns true ONLY for RUNNING + WAITING_CHILDREN, controlling Send button + text input visibility.

## Tasks

### 3.1 — SseService: Injection Event Handling

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add injection signals to SseService | Add new signal: `pendingInjection = signal<InjectionEvent \| null>(null)`. Define `InjectionEvent` interface: `{instance_id: string, event_type: string, content: string\|null, timestamp: string}`. | `frontend/src/app/services/sse.service.ts` |
| 2 | Register SSE event listeners for injection events | In SseService's event setup, add 3 new `eventSource.addEventListener()` calls for `injection_pending`, `injection_consumed`, `injection_cleared`. Parse JSON data and update `pendingInjection` signal. | `frontend/src/app/services/sse.service.ts` |
| 3 | Handle `injection_pending` event | On `injection_pending`: set `pendingInjection.set(event)`. The chat UI will reactively show the pending message. | `frontend/src/app/services/sse.service.ts` |
| 4 | Handle `injection_consumed` event | On `injection_consumed`: set `pendingInjection.set(null)`. The chat UI will remove the pending message visual. The message is now part of the normal conversation (the LLM will respond to it). | `frontend/src/app/services/sse.service.ts` |
| 5 | Handle `injection_cleared` event | On `injection_cleared`: set `pendingInjection.set(null)`. Same visual effect as consumed — pending message removed from UI. | `frontend/src/app/services/sse.service.ts` |
| 6 | Add fallback polling for injection status | Add method `fetchPendingInjection(instanceId: string)` that calls `GET /api/instances/{id}/injection`. Call on initial chat load and on SSE reconnection (if applicable). Update `pendingInjection` signal from response. | `frontend/src/app/services/sse.service.ts` |

### 3.2 — Chat Interface: Pending Message Display

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7 | Display pending injected message in chat UI | In chat-interface component: read `sseService.pendingInjection()` signal. If not null, render the pending message at the bottom of the message list with a DISTINCT visual style — e.g., dashed border, "Pending injection" label, different background color (amber/warning), pulsing animation. This is NOT a normal message — it's a "queued for injection" indicator. | `frontend/src/app/components/chat-interface/chat-interface.component.html`, `.ts` |
| 8 | Add distinct styling for pending injection message | Style the pending injection message card: amber/yellow background, dashed border, "⏳ Pending injection" badge, slightly muted text. Ensure it's visually distinct from normal user messages and assistant messages. Include a subtle pulse animation to indicate it's waiting. | `frontend/src/app/components/chat-interface/chat-interface.component.scss` |

### 3.3 — Message Input: canInject Computed (C6)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Add `canInject` computed signal (C6) | Add a NEW computed signal — do NOT modify `isInstanceRunning()`: ```typescript readonly canInject = computed(() => { const status = this.instanceStatus(); return status === 'running' \|\| status === 'waiting_children'; });``` This returns true ONLY for RUNNING and WAITING_CHILDREN (NOT QUEUED). | `frontend/src/app/components/message-input/message-input.component.ts` |
| 10 | Modify template to show Send + text input when canInject | When `canInject()` is true: show BOTH text input field AND Send button, ALONGSIDE the Pause button (which is controlled by `isInstanceRunning()`). Layout: text input takes most width, Send button and Pause button side by side on the right. When `canInject()` is false but `isInstanceRunning()` is true (i.e., QUEUED): show Pause button only (existing behavior). | `frontend/src/app/components/message-input/message-input.component.html` |
| 11 | Wire send action for injection mode | When user clicks Send while `canInject()` is true: call the same `POST /api/instances/{id}/messages` endpoint (the backend routes to injection for RUNNING/WAITING_CHILDREN). On success (202), the `injection_pending` SSE event will update the UI. Optionally clear the text input on 202. | `frontend/src/app/components/message-input/message-input.component.ts`, `frontend/src/app/pages/chat/chat.component.ts` |
| 12 | Update keyboard handling for injection mode | Modify `onKeydownEnter()`: when `canInject()` is true and user presses Enter (no Shift), trigger the send/injection action. Shift+Enter still creates a new line. Existing PAUSED (Enter resumes) and IDLE (Enter sends) logic unchanged. | `frontend/src/app/components/message-input/message-input.component.ts` |
| 13 | Verify `isInstanceRunning()` is UNCHANGED (C6) | Confirm `isInstanceRunning()` still returns true for RUNNING + WAITING_CHILDREN + QUEUED. The Pause button visibility for QUEUED must NOT break. Only `canInject` controls the new Send button + text input. | `frontend/src/app/components/message-input/message-input.component.ts` |

## Key Files
- `frontend/src/app/services/sse.service.ts` — New injection signals + SSE event listeners + fallback polling
- `frontend/src/app/components/chat-interface/chat-interface.component.ts` — Read pendingInjection signal
- `frontend/src/app/components/chat-interface/chat-interface.component.html` — Render pending message
- `frontend/src/app/components/chat-interface/chat-interface.component.scss` — Distinct styling
- `frontend/src/app/components/message-input/message-input.component.ts` — `canInject` computed + send action + keyboard handling
- `frontend/src/app/components/message-input/message-input.component.html` — Text input + Send + Pause layout
- `frontend/src/app/pages/chat/chat.component.ts` — Wire send action for injection mode

## Button Visibility Matrix (C6)

| Status | `isInstanceRunning()` | `canInject()` | Pause Button | Send Button | Text Input |
|--------|----------------------|---------------|-------------|-------------|------------|
| RUNNING | true | **true** | ✅ | ✅ (NEW) | ✅ (NEW) |
| WAITING_CHILDREN | true | **true** | ✅ | ✅ (NEW) | ✅ (NEW) |
| QUEUED | true | **false** | ✅ | ❌ | ❌ |
| PAUSED | false | false | ❌ (Resume shown) | ❌ | ✅ (for resume msg) |
| IDLE/other | false | false | ❌ | ✅ | ✅ |

## Constraints
- **Do NOT modify `isInstanceRunning()` (C6)**: This method controls Pause button visibility for RUNNING + WAITING_CHILDREN + QUEUED. Modifying it would break the Pause button for QUEUED instances. Add `canInject` as a separate computed.
- **Angular signals**: Use the existing signal-based pattern. All new state should be signals, not subjects/observables.
- **No breaking changes to existing toggle**: The 3-way toggle for PAUSED (Resume) and IDLE (Send) must continue to work exactly as before. Only the RUNNING/WAITING_CHILDREN branch gains a new Send + text input alongside the existing Pause button.
- **QUEUED is NOT injectable**: Only RUNNING and WAITING_CHILDREN allow injection. `canInject` returns false for QUEUED.
- **Visual distinction**: The pending injection message must be clearly different from normal messages — users should understand it's "waiting to be injected" not "already sent".
- **SSE event names**: Must exactly match backend: `injection_pending`, `injection_consumed`, `injection_cleared` (via `stream_message` with custom event_type).
- **Fallback polling**: The `GET /api/instances/{id}/injection` endpoint is for initial load and reconnection. Don't poll continuously.

## Deliverables
- [ ] SseService handles all 3 injection SSE events
- [ ] `pendingInjection` signal updates correctly on each event
- [ ] Fallback polling fetches injection status on load
- [ ] Chat UI shows pending injected message with distinct visual style
- [ ] `canInject` computed signal added (C6)
- [ ] `isInstanceRunning()` UNCHANGED — still includes QUEUED (C6)
- [ ] Message input allows text entry + send while RUNNING (alongside pause button)
- [ ] QUEUED state still shows Pause-only (no regression)
- [ ] PAUSED state Resume button still works (no regression)
- [ ] IDLE state Send button still works (no regression)
- [ ] Keyboard Enter triggers injection when `canInject()` is true
- [ ] Pending message clears on `injection_consumed` or `injection_cleared` event
