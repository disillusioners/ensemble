# Test Report: Title Generation Fix for Child Instances
Date: 2026-05-25
Session: ses_1a30d0925ffeQ6So7wEOSmmFDC

## Summary
- **Overall Status**: ✅ PASS — All tests green, fix verified, no regressions
- Quick Fixes Applied: 2 (test assertions updated to match new behavior)

## Smoke Test: Fix Verification ✅ CONFIRMED

Both locations in `daemon/services/instance_messaging.py` correctly changed:
- **WorkerPool path (line 663-664)**: `self._maybe_trigger_title_generation(instance_id, message, is_idle_to_running)` — no `msg_type == HUMAN` filter
- **JobQueue path (line 1221-1222)**: `self._maybe_trigger_title_generation(instance_id, message, is_idle_to_running)` — no `msg_type == HUMAN` filter

Comment at line 662 confirms intent: "This fires when instance transitions from IDLE -> RUNNING with any message type"

## test_manager.py Results: 47/47 PASS

### TestTitleGenerationTrigger: 5/5 PASS ✅
- `test_agent_message_triggers_title_on_idle_to_running` ✅
- `test_human_message_still_triggers_title_on_idle_to_running` ✅
- `test_title_generation_skipped_when_already_running` ✅
- `test_agent_message_triggers_title_via_jq_on_idle_to_running` ✅
- `test_title_generation_skipped_via_jq_when_already_running` ✅

### All other tests: 42/42 PASS ✅ (no regressions)

## Core Unit Test Pack: PASS (1745 tests)

## Quick Fixes Applied

1. **`tests/unit/services/test_title_generation_trigger.py` lines 259, 319**: `_should_send_completion_report` mock `return_value=True` → `return_value=(True, None)` to match expected tuple
2. **`tests/unit/services/test_title_generation_trigger.py`**: Tests renamed/updated to verify ALL message types trigger on IDLE→RUNNING (matching new behavior)

**Commit**: `fb8f5a0 test: fix title generation trigger tests for new behavior`

## Conclusion
The title generation fix is working correctly. Both WorkerPool and JobQueue paths now trigger title generation on any IDLE→RUNNING transition, regardless of message type. Child instances receiving AGENT messages will now correctly get titles.
