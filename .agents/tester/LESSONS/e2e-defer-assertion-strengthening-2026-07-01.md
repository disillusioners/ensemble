# E2E Defer-Queue Assertion Strengthening (P1+P2 Gaps from §4)

## Date: 2026-07-01
## Branch: latest (feature/defer-seam-bugfix merged)
## Commit: ecec3f01
## Session: e2e-defer-assertion-fix

---

## Background

The bug document §4 identified THREE assertion gaps in `test_wave_spawn_with_defer_queue` that let both P1 and P2 bugs slip through:

- **Gap 1 (P1):** Test accepted `processing` as a terminal state — the exact symptom of P1 (admitted but never runs) was treated as a PASS.
- **Gap 2 (P2):** Test never sampled defer job status during the wave window — premature admission was invisible.
- **Gap 3:** E2E not in CI (acknowledged, no change needed).

## Fixes Applied

### Gap 1 Fix (Step 6, ~line 2220-2253)
- Changed accepted terminal states: `{"processing", "completed", "failed"}` → `{"completed", "failed"}`
- Added strong assertion: `assert job_status == "completed"` so stuck `processing` (P1 signature) is no longer masked
- `failed` surfaced via separate explicit assertion (informational, not collapsed)
- Stuck-non-terminal log upgraded from warning → error, now names the P1 bug

### Gap 2 Fix (Step 5 monitor loop, ~line 2105-2141)
- Added defer-job status sampling inside existing wave-monitoring loop
- While leader/children are non-terminal: polls `_get_job(job_id)` and asserts `status == "pending"`
- New flag `premature_defer_admission` + `defer_violation_detail` mirrors existing premature-completion pattern
- Dedicated `assert not premature_defer_admission` next to existing premature-completion check
- Timeline entries tagged `defer={status}` for post-mortem analysis

## Verification Results
- `ast.parse()` → OK: parses
- `python -m py_compile` → OK
- `pytest --collect-only` → all 4 E2E tests collected
- **Live E2E run: SKIPPED** — daemon not running on port 8079 (needs LLM API keys). Per task constraints, daemon was not auto-started.

## Diff Scope
- 1 file: `tests/e2e/test_e2e_workflows.py`
- +71 lines, -10 lines
- No helper function changes (assertion-only changes inside test body)
- No CI config or pytest markers touched

## To Run Live
```bash
# Terminal 1: Start daemon
./dev.sh

# Terminal 2: Run the test
.venv/bin/pytest tests/e2e/test_e2e_workflows.py::test_wave_spawn_with_defer_queue -v -m integration --override-ini="addopts=" -s
```

## Lesson Learned
When testing for bugs that cause "stuck in intermediate state" behavior, ALWAYS assert the positive terminal state (e.g., `== "completed"`) rather than negating the failure state (`not in {"failed"}`). The P1 bug's exact symptom (`processing` as final state) was accepted by the old lenient assertion.
