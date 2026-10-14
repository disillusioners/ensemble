# Phase 2 Implementation — Task↔Job Feedback Loop

## Summary
Implemented the primary job completion mechanism connecting instance lifecycle to job state.

## Key Architecture Decisions
1. **EventBus vs LiveEventHub**: The initial implementation incorrectly used `self._live_hub.stream_lifecycle()` which only broadcasts to SSE connections. Fixed to use `self._event_bus.create_event()` which properly routes through `_broadcast_to_global()` to subscriber queues.

2. **Event flow**: `_publish_instance_lifecycle_event()` → `EventBus.create_event()` → `_broadcast_to_global()` → subscriber queues → `JobFeedbackObserver._process_event()`

3. **Observer event filtering**: Uses `event["event_type"] == "instance_lifecycle"` (NOT `kind`). This is the correct EventBus field.

4. **Race condition handling**: `terminate_instance()` always wins because it runs synchronously. Observer's `atomic_transition()` gets `rowcount=0` and skips silently.

5. **Cancellation cascade**: Uses existing `terminate_instance()` instead of creating new `cancel_instance()`. Does FAILED→CANCELLED second transition.

6. **Startup ordering**: recovery → observer → processor

## Files Created
- `daemon/services/job_feedback_observer.py` — EventBus subscriber
- `daemon/services/job_recovery_service.py` — Startup orphan recovery

## Files Modified
- `daemon/repositories/event/models.py` — INSTANCE_LIFECYCLE EventKind
- `daemon/manager.py` — _publish_instance_lifecycle_event(), parent_id fix, dead code removal
- `daemon/services/job_queue_service.py` — cancellation cascade
- `daemon/services/job_state_machine.py` — FAILED→CANCELLED transition
- `daemon/api.py` — wiring

## Commits
- `c1ab3f8` — Initial Phase 2
- `dd6a200` — Review fixes (EventBus integration, observer drain, recovery error handling)
