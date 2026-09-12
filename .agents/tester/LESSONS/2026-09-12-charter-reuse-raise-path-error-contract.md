# Quick Fix — generate_chart raise-path error contract (charter-reuse final gate)

**Date:** 2026-09-12
**Commit:** `25053ca4` on `feature/generate-chart-charter-reuse` (on top of `438cdd20`)
**Found by:** acceptance-walk gap-close probe (worker `cr-acceptance-walk`), final-shape gate
**Fixed by:** same worker, quick-fix authorization (criteria met: <20 lines, single file, obvious root cause, low risk)

## Symptom

The leader's acceptance contract for charter reuse states the legacy single-shot
`generate_chart` path must return an `"Error: ..."` string and **never raise**.
Existing coverage pinned only the `None`-result case
(`test_generate_chart_handles_none_result_as_error`, tests/test_chart_tools.py:200).
A new probe injecting `side_effect=RuntimeError("boom")` into `invoke_agent_and_wait`
showed the exception **propagates out of the tool coroutine** into the LLM tool loop.

## Root cause

`daemon/tools/chart_tools.py` (~:499 pre-fix): the legacy single-shot
`await invoke_agent_and_wait(...)` had **no try/except**. Only the `None`-content
branch was handled (`if result is None: return "Error: Charter agent timed out or
failed..."`). The comment claiming "invoke_agent_and_wait returns 'Error: ...' on
failure" was not true for the raise failure mode.

## Fix

Wrap the invoke await in `try/except Exception → return f"Error: Charter agent
invocation failed: {exc}"`, mirroring the None branch's tone. Reuse paths untouched
(audit-verified: reuse spawns via `enqueue_message`, not the direct invoke; reuse
degradation already guarded per T8.12).

## Verification (non-vacuity: test failed pre-fix with the exact symptom)

1. `tests/test_chart_tools_legacy_error_contract.py` → 2/2 PASS (raise case + None control)
2. 3-file charter pack (unit + integration + new pin) → 37/37 PASS in 1.80s
3. `test/packs/regression_unit_tools_test.sh` re-run → PASS 2,579P/5S — exact same
   counts as the morning partition run (+0F), proving no blast-radius damage

## Lesson

- **Probe BOTH failure modes of a seam you stub.** The branch's own tests covered
  `None` returns but never the raise path; the "never-raise" contract was only
  half-pinned. When a tool's error contract says "returns Error string", always pin
  raise-propagation too, not just sentinel returns.
- Gap-closes during verification gates are cheap defect mines: the reproducer file
  doubled as the regression pin (committed with the fix in one path-scoped commit).
