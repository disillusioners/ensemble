# Lesson: Governor D9 E2E Testing Limitations

**Date:** 2026-07-25
**Feature:** Governor Council-Manager Agent
**Context:** E2E test for governor — D9 series scenarios could not be validated via real E2E

## Problem

The phase4-plan.md defines E2E scenarios for the D9 series (Rev 3):
- **D9-1 / Scenario C2**: 1 result → degraded synthesis with "⚠️ Confidence Notice:"
- **D9-2 / Scenario D**: Councilor RUNNING at 30min → governor extends deadline once
- **D9-3 / Scenario D2/D3**: Councilor still RUNNING at 1h → hard kill, partial result
- **D9-4**: Extension past 1h hard cap rejected

These could not be validated in a real E2E test (5-min pack cap).

## Root Cause

**All D9 critical constraints are 100% prompt-driven, not Python-guarded.**

The quorum requirements, deadline values (30min soft, 1h hard), max councilors, and degraded-notice format are documented in `agents/governor/workflow.md`, `rule.md`, and `tools_note.md` as **guidance for the governor LLM**. There are no Python runtime guards that enforce:
- "If 1 result → prepend degraded notice"
- "If elapsed > 30min and councilor RUNNING → extend"
- "If elapsed > 1h → terminate and capture partial"

The governor's LLM reads the manifest (via `shared_context_metadata`), checks councilor status (via `get_instance_info`), and **decides** based on the prompt guidance.

## Why Integration Tests Are Sufficient (For Now)

The 40 integration tests in `test_governor_integration.py` + `test_council_tools.py` + `test_inject_allowed_models.py` verify:
- The **manifest schema** has the right fields (`deadline`, `deadline_hard_cap`, `deadline_extended`)
- The **tool logic** (`spawn_councilor`, `clear_councilor_errors`) works correctly
- The **appender** (`append_allowed_models`) injects the block

These confirm the **Python infrastructure** works. What they don't verify is whether the **LLM governor** actually follows the prompt instructions at runtime — that's inherently non-deterministic.

## What Would Be Needed for True D9 E2E

Two options:

1. **Long-running manual E2E** (30-60 min): spawn a real governor with real councilors, wait for the deadlines to trigger naturally. Expensive in LLM costs and time.

2. **Mock-time-injected E2E harness**: manipulate the system clock so the governor "sees" 31min/61min elapsed without actually waiting. This requires:
   - A test-only hook to override `datetime.now()` or the manifest timestamps
   - A way to inject fake councilor statuses (RUNNING at 31min)
   - Asserting on the governor's LLM output (non-deterministic — may need multiple runs)

Option 2 is the recommended future test-architecture improvement. It would convert the D9 scenarios from "integration-tested infrastructure" to "behaviorally-validated E2E".

## Recommendation

- **For now**: Accept integration test coverage for D9 series. The infrastructure is verified; the LLM behavior is design-reviewed (planner/approver signed off on workflow.md).
- **Future**: Build a mock-time E2E harness if the governor enters production use with real councilors. This is a test-architecture task, not a bug fix.
- **Monitoring**: If the governor is used in production, add logging/alerting for: deadline extensions, hard kills, degraded synthesis outputs. This compensates for the lack of E2E behavioral validation.
