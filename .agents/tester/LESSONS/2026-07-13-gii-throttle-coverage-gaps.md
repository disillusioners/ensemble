# Coverage Gap Analysis: get_instance_info Throttle Tests
Date: 2026-07-13

## Context
Developer wrote 23 tests for the get_instance_info throttle feature. Review identified 8 coverage gaps. All gaps addressed with new tests.

## Key Findings

### 1. Parallel tool call semantics were documented but untested
The source code comment (daemon/graph.py:25-41) documents that parallel tool calls (multiple tool_calls in one AIMessage) cause ToolNode to interleave ToolMessages. If a non-gii message lands at messages[-1], the counter resets. This is intentional but had NO test.

**Fix:** `test_parallel_tool_call_resets_counter` now pins this behavior.

### 2. Error ToolMessages still bump the counter
The throttle checks `isinstance(last_msg, ToolMessage) and last_msg.name == GII_TOOL_NAME` — it does NOT inspect the `status` field. Error responses from get_instance_info still bump the counter. This is arguably the MOST important case to throttle (retry loops after errors).

**Fix:** `test_error_tool_message_still_bumps` documents this as intentional.

### 3. Reset trigger coverage was narrow
Original `test_non_gii_message_resets_counter` only tested AIMessage. Missing: HumanMessage, non-gii ToolMessage, AIMessage-with-tool_calls, empty messages.

**Fix:** 4 new tests cover all reset trigger variants.

### 4. Cap behavior at high counts
Original test covered count=7. The implementation has no upper bound on the counter value — bump keeps incrementing while delay stays at 900.

**Fix:** `test_cap_holds_at_high_counts` verifies count=20 returns 20 with delay=900.

### 5. Integration tests used stub slot, not real ToolThrottleSlot
The TestAgentNodeThrottleIntegration tests used _StubToolThrottleSlot (custom mock), not the real ToolThrottleSlot class. The real class was tested separately but never through the agent_node path.

**Fix:** `test_real_tool_throttle_slot_integration` exercises the full delegation chain.

## Pattern: asyncio.sleep mocking
All throttle tests mock asyncio.sleep to avoid real delays. The pattern:
- Use AsyncMock or patch to record calls without executing
- Verify sleep was called with expected delay values (180, 300, 600, 900)
- Verify sleep was NOT called for counts < 3
