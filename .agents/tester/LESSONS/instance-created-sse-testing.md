# instance_created SSE Event — Testing Lessons

## Feature Overview
- Backend emits `instance_created` SSE events when instances are spawned
- Child instances → parent's SSE stream via `LiveEventHub.stream_instance_created()`
- Root instances → global notifications stream via `NotificationBroadcaster.emit_instance_created()`
- Frontend drains queue via Angular effect and adds instances to tree

## Key Findings
1. **Three-layer KB filtering**: Broadcaster (backend) → NotificationService (frontend global) → InstanceService (frontend queue) — all filter KB agents correctly
2. **Graceful degradation**: If parent not in frontend list, child added as root; tree reconciled on next poll
3. **Queue-based batching**: Angular signal queue handles rapid spawning correctly; no explicit backpressure
4. **Deduplication**: Frontend checks instance_id before adding to tree

## Tests Added
- `tests/unit/test_live_event_hub.py`: 4 new tests for `stream_instance_created()` (commit `9a6e742`)
- `tests/unit/test_notification_broadcaster.py`: Already had 6 instance_created tests (23 total)

## Quick Fixes
- `tests/test_api.py:821`: Mock method `get_instance` → `get_instance_info` (commit `1efa9ba`)

## Pre-existing Failures (Not Related)
- 4 test failures unrelated to instance_created feature
- All classified as pre-existing (mock issues, behavior changes, test isolation)
