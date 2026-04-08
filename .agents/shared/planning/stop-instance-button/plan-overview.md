# Plan Overview: Stop Instance Button

## Objective
Add a stop button to the frontend that replaces the send button while an instance is actively processing, allowing users to cancel the current request. This requires a new backend endpoint to gracefully stop a running instance's current work without terminating the instance itself.

## Scope Assessment
**SMALL** — Single cohesive feature spanning FE and BE. The backend already has a full cancellation infrastructure (`CancellationToken`, `ActiveRequestRegistry`, `CancellationCallbackHandler`). The work is primarily: (1) one new public manager method + API endpoint, (2) one new FE API method, (3) one button swap in the existing component.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Architecture**: Angular 17+ frontend (signals for internal state, `@Input()` decorators for component props) + FastAPI backend with LangGraph agent execution
- **Key insight**: The backend already broadcasts `cancelled` events when operations are cancelled — the frontend just doesn't listen for them yet and has no way to trigger cancellation.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend: Stop Endpoint | Add `POST /instances/{id}/stop` API endpoint that cancels active requests without terminating the instance | None | — | 30min |
| 2 | Frontend: Stop Button | Add stop button to message-input, wire up API call, handle cancelled SSE events | Phase 1 | loose | 45min |

### Coupling Assessment

| From → To | Coupling | Reasoning |
|-----------|----------|-----------|
| Phase 2 → Phase 1 | **loose** | Frontend only needs the API contract (`POST /instances/{id}/stop` → `{stopped: true, cancelled_requests: int}`). It doesn't depend on implementation details. Phase 2 can be coded in parallel as long as the endpoint contract is agreed. |

**Scheduling**: Can be done sequentially or with overlap (code Phase 2 once Phase 1 contract is clear, even before Phase 1 review).

## Architecture Analysis

### Current State

**Backend (already supports cancellation)**:
- `ActiveRequestRegistry.cancel_by_instance(instance_id)` — cancels all active requests for an instance, but **hardcodes `CancellationReason.INSTANCE_TERMINATED`** internally (line 128). The method accepts no `reason` parameter, so the fix requires adding one.
- `CancellationToken` + `CancellationCallbackHandler` — propagates cancellation to LLM/tool boundaries
- Manager broadcasts `cancelled` event on `OperationCancelledError`
- `DELETE /instances/{id}` — full terminate (destroys instance, cascades to children)

**Frontend**:
- `MessageInputComponent` — always shows a send button, disabled when `isSending()` (from parent) is true
- `ChatComponent.isSending` signal — tracks whether a message is being processed
- `SseService.isStreaming` signal — tracks SSE connection streaming state
- `SseService.disconnect()` — closes SSE connection
- No `cancelled` event listener in SSE service

### Gap: No "soft stop" endpoint
The existing `DELETE /instances/{id}` is a **full terminate** (destroys instance, removes from memory, cascades to children, updates DB status to `terminated`). We need a lighter operation that just **cancels the current in-flight request** and lets the instance return to `idle` state.

## Detailed Design

### Backend: `POST /instances/{instance_id}/stop`

```
POST /api/instances/{instance_id}/stop
→ { "stopped": true, "cancelled_requests": 2 }
```

**Logic** (in `api.py`):
1. Validate instance exists (same pattern as `terminate_instance`)
2. Call `manager.cancel_instance_requests(instance_id, reason=USER_STOPPED)` — a new **public** method on `InstanceManager` (not `_request_registry` directly)
3. Return `{ stopped: true, cancelled_requests: N }`

**Key difference from terminate**: Does NOT destroy the instance, does NOT cascade to children, does NOT update DB status to `terminated`. The instance remains alive and can receive new messages after cancellation completes.

**Returns when idle**: `{ stopped: true, cancelled_requests: 0 }` — safe to call on an already-idle instance with no active requests.

**Required changes to existing code**:
- `ActiveRequestRegistry.cancel_by_instance()` — add a `reason` parameter (currently hardcoded to `INSTANCE_TERMINATED` on line 128)
- `InstanceManager` — add `cancel_instance_requests()` public method (calls registry with reason, returns count)
- `CancellationReason` enum — add `USER_STOPPED`

### Frontend: Stop Button

**API Service** (`api.service.ts`):
- Add `stopInstance(instanceId: string)` method → `POST /instances/{instanceId}/stop`

**Message Input Component** (`message-input.component.ts` + `.html`):
- Add `@Input() isStreaming = false` — plain boolean, following the project's `@Input()` decorator pattern (not signal inputs)
- Add `@Output() stopRequested = new EventEmitter<void>()`
- In template: conditionally show stop button when `isStreaming` is true (use `@if (isStreaming)` — no parentheses, since it's a plain `@Input()` value, not a signal)
- Stop button: red/orange styling, square-stop icon SVG
- On click → emit `stopRequested`
- **UX note**: The textarea remains disabled during streaming — it is already controlled by `[disabled]="disabled"` where the parent passes `isSending()`

**Chat Component** (`chat.component.ts` + `.html`):
- Add `onStopInstance()` method that calls `api.stopInstance(instanceId)`
- Pass `isStreaming` to `<app-message-input>` as `[isStreaming]="isSending()"` (signal call to get current value before passing)
- Wire `(stopRequested)` to `onStopInstance()`
- Handle response: `isSending.set(false)`, the cancelled SSE event will also reset `isStreaming`

**SSE Service** (`sse.service.ts`):
- Add event listener for `cancelled` events
- On `cancelled` event: set `isStreaming.set(false)`, clear partial messages

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Race condition: stop request arrives after processing completes naturally | Low | Backend returns `cancelled_requests: 0` — frontend handles gracefully. SSE `completed` event will reset state naturally. |
| Cancellation not instant (cooperative, checks at LLM/tool boundaries) | Low | Acceptable UX — the stop triggers within seconds at the next LLM call or tool execution boundary. Backend already has `CancellationCallbackHandler`. |
| Multiple rapid stop clicks | Low | The API call is idempotent — if already cancelled, returns `cancelled_requests: 0`. No double-click guard needed. |
| `cancelled` SSE event arrives before stop API response | Low | Both paths converge to same state (`isStreaming=false`). The effect-based architecture handles this naturally. |

## Success Criteria
- [ ] `POST /instances/{id}/stop` endpoint exists and cancels active requests without destroying the instance
- [ ] `cancelled` event carries `reason: "user_stopped"` in SSE
- [ ] Send button is replaced with a visually distinct stop button when instance is streaming
- [ ] Clicking stop cancels the current request and the instance returns to idle
- [ ] Partial/streaming message is cleaned up after stop
- [ ] Instance can receive new messages after being stopped
- [ ] Returns `{ stopped: true, cancelled_requests: 0 }` when called on an already-idle instance (no active requests to cancel)

## Tracking
- Created: 2025-04-08
- Last Updated: 2025-04-08
- Status: draft
