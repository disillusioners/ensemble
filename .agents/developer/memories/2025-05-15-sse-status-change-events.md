# SSE Status Change Events Implementation

## Date: 2025-05-15

## What was implemented
Real-time SSE events for instance status changes (idle, running, terminated) so the frontend updates immediately instead of waiting for 10s polling.

## Architecture

### Backend
- `LiveEventHub.stream_status_change(instance_id, status)` — new method in `daemon/services/live_event_hub.py`
- Hooks into status transitions in `instance_lifecycle.py` and `instance_messaging.py`
- Events follow existing SSE format: `{instance_id, event_type: "status_change", status}`
- SSE endpoint in `daemon/routers/messages.py` serializes events as `event: status_change\ndata: {...}\n\n`

### Frontend
- `sse.service.ts`: `statusChange` signal parses `status_change` events from EventSource
- `instance.service.ts`: `updateInstanceStatus()` does optimistic local update + `effect()` wires SSE to state
- `chat.component.ts`: `currentInstance` changed from `WritableSignal` to `computed` signal derived from `instanceService.instances()` and `currentInstanceId`
- `models/index.ts`: Added `'status_change'` to `SseEventType` union type

## Key Patterns
- **Optimistic updates**: SSE updates local state immediately, 10s polling corrects any inconsistencies
- **Fire-and-forget for spawn**: `asyncio.create_task()` used for non-critical status emission at spawn time
- **LiveEventHub for live-only events**: No DB persistence needed for status changes — they're ephemeral
- **computed signal pattern**: Changing `currentInstance` to computed ensures it auto-updates when `instances` signal changes

## Testing
- 4 unit tests added in `tests/unit/test_live_event_hub.py` for `stream_status_change`
- Existing test fixtures updated to mock the new method
