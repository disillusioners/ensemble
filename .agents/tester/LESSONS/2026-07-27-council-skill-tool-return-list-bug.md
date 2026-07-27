# Lesson: convene_council_with_skill tool missing from return list

**Date:** 2026-07-27
**Feature commit:** `efc652bc` (convene_council_with_skill)
**Severity:** 🔴 Functional bug (tool invisible to agents on clean checkout)
**Status:** Uncommitted fix exists in working tree; not yet committed by developer

## Symptom
The `convene_council_with_skill` tool function is defined inside `create_instance_tools()` in `daemon/tools/instance.py`, but was **NOT added to the tuple of tools returned** by the function. On a clean checkout of `efc652bc`, `create_instance_tools()` does not return the new tool → agents never see it → the feature is dead code.

## Root Cause
Forgotten registration in the return list. When adding a new tool to a factory function with a long return tuple (`create_instance_tools` returns dozens of tools), it's easy to define the tool but forget to append it to the return statement.

## Secondary finding (security)
The original code interpolates `councilor_skill` directly into the governor's message string:
```python
message_text = (
    f'Convene a council using councilor_agent_id="{canonical}".\n'
    f"Councilor skill: {councilor_skill}\n"   # <-- direct interpolation
    ...
)
```
A `councilor_skill` containing `\n` could inject arbitrary lines into the governor prompt (prompt injection via the skill name). The uncommitted working-tree change adds a newline guard.

## Fix (uncommitted in working tree)
1. Added `convene_council_with_skill` to the return list in `create_instance_tools()` (~line 1384).
2. Added newline-injection guard:
   ```python
   if "\n" in councilor_skill or "\r" in councilor_skill:
       raise ValueError("councilor_skill must not contain newlines")
   ```

## How it was caught
During post-test git verification (tester checking that the test commit was clean), the tester noticed `daemon/tools/instance.py` was Modified. Investigating the diff revealed both the missing-return-list bug and the newline guard — neither was made by the test workers.

## Test coverage
- Test #12 (`test_convene_council_with_skill_registered_as_council_category`) asserts the tool is present in the factory output. **This test passes against the working tree (which has the fix) but would FAIL on a clean `efc652bc` checkout.** Once the developer commits the fix, the test guards against regression.
- No test currently covers the newline-injection guard. **Recommendation:** add a test `test_convene_council_with_skill_newline_raises` asserting `councilor_skill="a\nb"` raises ValueError.

## Prevention pattern
When adding a new tool to `create_instance_tools()`, the definition AND the return-list entry must land in the same commit. A checklist item for this factory: "new tool added to the return tuple."
