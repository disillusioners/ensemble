# ensure.md Validation Lessons — skill_feedback upgrade (2026-07-21)

**Outcome:** Clean PASS. No failures, no contradictions. This document records
the validation approach for future reference.

## What was validated
Scoped ensure.md requirements for the `feature/skill-feedback-upgrade` change
set (commit `da5ef6ee`). The key async-conversion concern was that
`skill_trigger_engine.py` had three methods converted from sync to async
(`_eval_low_usefulness`, `_evaluate_condition`, `_build_reason`) with DB repo
calls wrapped in `asyncio.to_thread`.

## Pattern: static-check validation for async-conversion safety

When a change converts sync methods to async and wraps DB calls in
`asyncio.to_thread`, the critical safety invariants are:

1. **Every DB repo call inside an async method is wrapped** — grep for
   `_repo.` / `self.<name>_repo.` patterns and confirm each is inside an
   `await asyncio.to_thread(...)` block (or runs in a closure passed to it).
2. **Every caller awaits the converted function** — grep for the function name
   and confirm each call site is preceded by `await`.
3. **No dead code** — the converted functions are still called (covered by #2).

This is a fast, read-only validation that complements (but does not replace)
the `concurrency_atomic_unit_test` pack when the change touches concurrency code.
For this change set, concurrency/atomic code was NOT touched — only the trigger
engine — so the static check was sufficient and the heavy pack was correctly
out of scope.

## Grep recipe used
```bash
# R2: confirm all DB calls wrapped
grep -n "asyncio.to_thread\|\.get_avg_usefulness\|skill_repo.get\|usage_repo\." daemon/services/skill_trigger_engine.py
# broader: catch any repo call
grep -n "_repo\.\|self\.trigger_repo\|self\.skill_repo\|self\.metrics_service\." daemon/services/skill_trigger_engine.py

# R3: confirm all callers await
grep -n "_eval_low_usefulness\|_evaluate_condition\|_build_reason\|await " daemon/services/skill_trigger_engine.py
```

Both must be paired with line-context reading — grep alone can miss a call that
spans multiple lines or runs inside a closure. The `_list_skills` closure pattern
(L273-276) is a good example: the repo call is on L274 but the wrapping is on L276.

## No issues found
All repo calls wrapped. All callers await. No dead code. No contradictions with
ensure.md requirements.
