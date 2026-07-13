# Test Report: get_instance_info Consecutive Call Throttling
Date: 2026-07-13T21:38 UTC
Branch: feature/instance-info-throttle
Commit: 9221931c (test additions), 411757be (feature)

## Summary
- Total: 31 tests | Passed: 31 | Failed: 0 | Errors: 0
- Unit Tests: 31 tests
- Quick Fixes Applied: 0 (no bugs found in source)
- Quarantined: 0
- Runtime: 0.95s

## Scope Decision
> Full test suite NOT run. Change touches only `daemon/graph.py` (throttle block in agent_node) and `daemon/manager.py` (_gii_throttle dict + methods). Isolated single-feature change → ran only `tests/test_gii_throttle.py`. Full suite not warranted.

## Session IDs
- gii-review (ses_0a28ee249ffeQeRATdKmqO3cCJ) — coverage analysis
- gii-run-existing (ses_0a28ee24effeC2exUMNS4iVbrz) — existing test run
- gii-write-tests (ses_0a28cff37ffetw74Dfe43C36Z0) — additional test writing
- gii-verify (ses_0a2870f00ffeUnS0spZ2CBL9Bb) — final verification
- gii-commit (ses_0a2860cf4ffeqAxP6IuEiksv3W) — commit changes

## Coverage Analysis

### Existing Tests (23) — What's Covered
- InstanceManager counter methods: bump (start at 1, increment), reset (zeroes, safe on empty), get_count (unset returns 0), multi-instance isolation
- _cleanup_instance_state: clears _gii_throttle, safe when no entry
- ToolThrottleSlot: bump/reset/get_count delegation, getattr(None) safety
- Delay map constants: GII_DELAY_MAP values, GII_MAX_DELAY=900, GII_TOOL_NAME
- agent_node integration: counts 1/3/4/5/6/7 (no sleep / 180/300/600/900/900), AIMessage reset, throttle_slot=None safe

### Gaps Identified & Tests Added (8)

| Priority | Test | What It Verifies |
|----------|------|------------------|
| P1 | test_parallel_tool_call_resets_counter | messages[-1] is non-gii ToolMessage → counter resets. Pins intentional parallel-tool interleaving design. |
| P1 | test_error_tool_message_still_bumps | ToolMessage(status="error", name="get_instance_info") → counter bumps. Errors count as consecutive calls. |
| P1 | test_non_gii_tool_message_resets | Non-gii ToolMessage at messages[-1] after gii sequence → reset. Most common real-world reset path. |
| P2 | test_human_message_resets_counter | HumanMessage between gii calls → reset. User interruption mid-loop. |
| P2 | test_cap_holds_at_high_counts | 20 consecutive gii → bump returns 20, delay stays at 900. Cap is true cap, bump doesn't saturate. |
| P2 | test_real_tool_throttle_slot_integration | Real ToolThrottleSlot wrapping _ManagerStub → full delegation chain exercised end-to-end. |
| P3 | test_aimessage_with_tool_calls_resets | AIMessage with tool_calls=[gii] at messages[-1] → reset. Documents boundary behavior. |
| P3 | test_empty_messages_list_does_not_raise | messages=[] → no exception, reset called (safe no-op). Tests messages[-1] if messages else None guard. |

## Test Results
- TestManagerThrottle: 8/8 PASS
- TestToolThrottleSlot: 4/4 PASS
- TestDelayMap: 3/3 PASS
- TestAgentNodeThrottleIntegration: 16/16 PASS (8 original + 8 new)

## Bugs Found in Source Code
None. All new tests passed on first run, confirming the implementation matches the documented design.

## Implementation Notes (from review)
- The throttle only detects consecutive SINGLE-tool gii calls. Parallel tool calls in one AIMessage cause ToolNode to interleave ToolMessages; if a non-gii message lands at messages[-1], the counter resets. This is intentional design, now pinned by test_parallel_tool_call_resets_counter.
- Error ToolMessages (status="error") still bump the counter — name match is the sole criterion. This is the correct behavior for throttling retry loops.
- No try/except around throttle_slot calls in agent_node — if bump raises, agent_node crashes. Low risk but noted.
- asyncio.sleep is mocked in all tests; no real delays.

## Documentation Updated
- [x] PACKS.md — added gii_throttle_unit_test entry, updated summary counts
- [x] RESULTS/2026-07-13-gii-throttle-tests.md — this report
- [x] LESSONS/ — coverage gap analysis lessons

## Code Changes Summary
- tests/test_gii_throttle.py — +318/-1 lines (8 new tests + 1 helper)
- Commit: 9221931c
- Source code (daemon/graph.py, daemon/manager.py): UNCHANGED — no bugs found

## Overall Status
- Unit Tests: ✅ PASS (31/31)
- ensure.md: N/A (no ensure.md requirements map to this test file)
- **Testing Complete**: ✅ READY
