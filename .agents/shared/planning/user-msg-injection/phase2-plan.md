# Phase 2: Backend API + SSE — State-Aware send_message, SSE via stream_message

## Objective
Make the `send_message` API endpoint state-aware so it routes to the RAM injection slot when the instance is RUNNING or WAITING_CHILDREN. Reuse the existing `stream_message()` method (W5) for SSE events. Add a query endpoint for frontend to poll pending injection status. Wire the SSE emission calls that Phase 1 set up as placeholders.

**Critical (C4)**: Do NOT change the PAUSED behavior of send_message. The existing auto-resume flow (resume_instance_cascade + resume_processing_job) remains untouched.

## Coupling
- **Depends on**: Phase 1
- **Coupling type**: tight
- **Shared files with other phases**: `daemon/manager.py` (calls Phase 1's `set_injection`/`clear_injection`), `daemon/graph.py` (finalizes SSE emission at consumption point using `live_hub` from Phase 1's factory closure), `daemon/services/instance_lifecycle.py` (finalizes SSE emission at pause clear point)
- **Shared APIs/interfaces**: SSE event contract (event types + payload shape via `stream_message`) — Phase 3 (frontend) depends on this
- **Why this coupling**: Phase 2 directly calls Phase 1's helper methods and uses the `live_hub` reference threaded through the factory closure. The SSE event contract defines what Phase 3 must handle.

## Context
- `send_message` API is at `POST /api/instances/{instance_id}/messages` in `daemon/routers/messages.py`
- **Current behavior (MUST NOT CHANGE for PAUSED — C4)**:
  - PAUSED → resume cascade + resume_processing_job (auto-resume with message), return 200
  - Normal → enqueue_message with source="api", return 200
- LiveEventHub is at `daemon/services/live_event_hub.py` with `stream_message()` method
- `stream_message()` accepts an `event_type` parameter — reuse it for injection events (W5)
- Events formatted as standard SSE with `event`, `id`, `data` fields

## Tasks

### 2.1 — SSE via stream_message (W5)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define injection SSE event contract via stream_message | Three event types reusing `stream_message()`: `injection_pending`, `injection_consumed`, `injection_cleared`. Payload: `{"instance_id": str, "event_type": str, "content": str\|null, "timestamp": str}`. Call `stream_message(instance_id, event_type="injection_pending", data=payload)` etc. No new method on LiveEventHub — just use existing `stream_message()` with different `event_type` values. | `daemon/services/live_event_hub.py` (if stream_message needs adjustment to accept custom event types) |
| 2 | Verify stream_message accepts custom event_type | Check if `stream_message()` already accepts an `event_type` parameter. If it only supports fixed event types, add an optional `event_type` parameter defaulting to the existing value. Minimal change — do NOT create a new method. | `daemon/services/live_event_hub.py` |

### 2.2 — State-Aware send_message API (C4)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 3 | Add state-aware routing to send_message | In `daemon/routers/messages.py` `send_message_to_instance()`: (1) Fetch instance status from DB; (2) **If RUNNING or WAITING_CHILDREN** → call `manager.set_injection(instance_id, content)`, emit `injection_pending` SSE via `stream_message`, return 202 Accepted with `{"status": "injected", "instance_id": ..., "content": ...}`; (3) **If PAUSED** → existing auto-resume behavior (NO CHANGE — C4), return 200; (4) **If IDLE or terminal** → existing enqueue_message path (NO CHANGE), return 200. | `daemon/routers/messages.py` |
| 4 | Add empty content validation (S4) | Before routing, validate: `if not message_data.content or not message_data.content.strip(): raise HTTPException(status_code=400, detail="Message content cannot be empty")`. Apply to the injection path (and optionally to all paths for consistency). | `daemon/routers/messages.py` |
| 5 | Add SSE emission on replacement (2nd message replaces 1st) | When setting injection on a slot that already has a pending injection: check `manager.get_injection(instance_id)` first. If not None, emit `injection_cleared` for the old content via `stream_message` BEFORE setting the new one, then emit `injection_pending` for the new content. | `daemon/routers/messages.py` |
| 6 | Add query endpoint for pending injection status | New endpoint: `GET /api/instances/{instance_id}/injection` → returns `{"instance_id": ..., "pending": bool, "content": str\|null, "timestamp": str\|null}`. Calls `manager.get_injection(instance_id)`. Fallback for frontend sync if SSE events are missed. | `daemon/routers/messages.py` |

### 2.3 — Finalize SSE Emission Hooks (from Phase 1)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 7 | Finalize SSE emission at injection consumption (agent node) | In `daemon/graph.py` `create_agent_node()`, after `injection_slot.clear(instance_id)` (Phase 1 Task 17-19): finalize the `live_hub.stream_message(instance_id, event_type="injection_consumed", data=payload)` call. The `live_hub` reference comes from Phase 1's factory closure (C1). Verify the call works with real LiveEventHub instance. | `daemon/graph.py` |
| 8 | Finalize SSE emission at pause clearing | In `daemon/services/instance_lifecycle.py`, after `manager.clear_injection(instance_id)` (Phase 1 Task 7): if a cleared entry was returned (not None), emit `injection_cleared` SSE via `live_hub.stream_message(instance_id, event_type="injection_cleared", data=payload)`. Need access to LiveEventHub — verify how instance_lifecycle.py accesses it (may be passed as parameter or accessed via manager). | `daemon/services/instance_lifecycle.py` |

### 2.4 — Unit Tests

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 9 | Unit tests for API state-aware routing | Test: RUNNING → injection path (202), IDLE → enqueue path (200), PAUSED → existing auto-resume (200, NO CHANGE), WAITING_CHILDREN → injection path (202), terminal → enqueue path (200). Test replacement: 2nd injection clears 1st (emits cleared then pending). Test empty content → 400 (S4). Test query endpoint returns correct status. | `tests/test_injection_api.py` (new) |
| 10 | Unit tests for SSE events via stream_message | Test: `stream_message` with `event_type="injection_pending"` formats correct SSE. Test: injection_pending emitted on set. Test: injection_consumed emitted on agent node consumption. Test: injection_cleared emitted on pause. Test: replacement emits cleared then pending. Verify NO new method was created on LiveEventHub — only `stream_message` reused. | `tests/test_injection_sse.py` (new) |

## Key Files
- `daemon/services/live_event_hub.py` — Verify/adjust `stream_message()` to accept custom event_type (W5)
- `daemon/routers/messages.py` — State-aware send_message (C4) + empty content validation (S4) + query endpoint
- `daemon/graph.py` — Finalize SSE emission at consumption (extends Phase 1 change)
- `daemon/services/instance_lifecycle.py` — Finalize SSE emission at pause clear (extends Phase 1 change)
- `tests/test_injection_api.py` — API routing unit tests (new)
- `tests/test_injection_sse.py` — SSE event unit tests (new)

## SSE Event Contract (for Phase 3)

All events use the existing `stream_message()` method with custom `event_type`:

```
Event: injection_pending
Data: {"instance_id": "abc", "event_type": "injection_pending", "content": "user message", "timestamp": "2026-07-12T..."}

Event: injection_consumed
Data: {"instance_id": "abc", "event_type": "injection_consumed", "content": "user message", "timestamp": "2026-07-12T..."}

Event: injection_cleared
Data: {"instance_id": "abc", "event_type": "injection_cleared", "content": "user message"|"null", "timestamp": "2026-07-12T..."}
```

## send_message API Routing (C4)

| Instance Status | Behavior | Return Code | SSE Event |
|----------------|----------|-------------|-----------|
| RUNNING | Set RAM injection slot | 202 | `injection_pending` |
| WAITING_CHILDREN | Set RAM injection slot | 202 | `injection_pending` |
| PAUSED | **Existing auto-resume (NO CHANGE)** | 200 | (existing status_change events) |
| IDLE | Normal enqueue_message | 200 | (existing message events) |
| Terminal | Normal enqueue_message | 200 | (existing message events) |

**CRITICAL**: The PAUSED branch in `send_message_to_instance()` must remain exactly as-is. It calls `resume_instance_cascade()` + `resume_processing_job()` — a load-bearing code path that supports vision image propagation. Do NOT return 409 for PAUSED.

## Constraints
- **Do NOT change PAUSED behavior (C4)**: The PAUSED branch in send_message remains untouched. No 409 rejection. Existing auto-resume with `resume_instance_cascade` + `resume_processing_job` continues to work.
- **Reuse stream_message (W5)**: Do NOT create a new `stream_injection()` method. Use `stream_message()` with custom `event_type` parameter. Minimal change to LiveEventHub.
- **Empty content validation (S4)**: Reject empty/whitespace-only content with 400 before routing.
- **SSE fire-and-forget**: Follow existing pattern — if no SSE connections exist for instance, event is silently dropped. Query endpoint serves as fallback.
- **Return codes**: 202 for injection (NEW). 200 for PAUSED auto-resume (UNCHANGED). 200 for normal enqueue (UNCHANGED).
- **DB status fetch**: Fetch current instance status from DB to route correctly. Use existing repository pattern.
- **WAITING_CHILDREN**: Treat same as RUNNING for injection. Slot persists, consumed on parent resume.
- **live_hub access**: The `live_hub` reference is threaded via factory closure (C1, Phase 1). For instance_lifecycle.py, verify how LiveEventHub is accessed — it may need to be passed as a parameter or accessed via manager.

## Deliverables
- [ ] `stream_message()` accepts custom `event_type` (or already does)
- [ ] `send_message` API routes correctly: RUNNING/WAITING_CHILDREN → injection (202), PAUSED → unchanged (200), IDLE/terminal → unchanged (200)
- [ ] Empty content rejected with 400 (S4)
- [ ] SSE events emitted via `stream_message` at all 3 lifecycle points (pending, consumed, cleared)
- [ ] Replacement emits cleared-then-pending event sequence
- [ ] Query endpoint `GET /api/instances/{id}/injection` returns pending status
- [ ] No new method created on LiveEventHub — only `stream_message` reused (W5)
- [ ] PAUSED auto-resume behavior unchanged (C4)
- [ ] Unit tests pass for API routing and SSE events
- [ ] No regressions in existing test suite
