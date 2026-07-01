# E2E Test Report: Defer-Queue Assertion Strengthening
Date: 2026-07-01
Session: e2e-defer-assertion-fix (ses_0e42d5433ffeCwGQNsZsD8f14r)
Commit: ecec3f01

---

## Summary
- **Task:** Strengthen E2E defer-queue test assertions (P1+P2 coverage gaps from bug doc §4)
- **Result:** ✅ PASS — Both gaps fixed, file verified, committed
- **Live E2E Run:** ⏸️ SKIPPED — daemon not running (needs LLM API keys)

---

## Gap Analysis & Fixes

### Gap 1 — Assertions too lenient (P1 coverage)
**Problem:** Test accepted `{"processing", "completed", "failed"}` as passing terminal states. P1 bug leaves job stuck in `processing` — the exact failure mode was masked as PASS.

**Fix:** 
- Accepted terminal states → ONLY `{"completed", "failed"}`
- Added `assert job_status == "completed"` — stuck `processing` now fails the test
- `failed` surfaced via separate assertion (informational)
- Log upgraded warning → error, names P1 bug

**Location:** ~line 2220-2253 (Step 6)

### Gap 2 — Defer-isolation invariant never asserted (P2 coverage)
**Problem:** Test never sampled defer job status during the ~90s wave window. Premature admission (P2 bug) was completely invisible.

**Fix:**
- Added periodic defer-job status polling inside existing wave-monitoring loop
- Asserts `status == "pending"` while any leader/child is non-terminal
- Uses existing `_get_job()` helper, `TERMINAL_STATUSES` set, and 2s poll cadence
- New flags `premature_defer_admission` / `defer_violation_detail` with dedicated assertion
- Timeline entries tagged `defer={status}` for post-mortem

**Location:** ~line 2105-2141 (Step 5 monitor loop)

### Gap 3 — CI exclusion (acknowledged, no change)
E2E tests carry `integration` marker, excluded from default suite. No CI config changes made.

---

## Verification

| Check | Result |
|-------|--------|
| `ast.parse()` | ✅ OK: parses |
| `python -m py_compile` | ✅ OK |
| `pytest --collect-only` | ✅ All 4 E2E tests collected |
| Daemon health probe (port 8079) | ❌ Not running (HTTP=000) |
| Live E2E run | ⏸️ SKIPPED — daemon needs LLM API keys |

---

## Code Changes Summary
- **File:** `tests/e2e/test_e2e_workflows.py`
- **Diff:** +71 lines, -10 lines
- **Commit:** ecec3f01 — `test: strengthen E2E defer-queue assertions (P1+P2 coverage gaps from §4)`
- No helper functions modified
- No CI config or pytest markers touched

---

## ensure.md Validation

### Critical Requirement: E2E Wave Spawn + Defer Queue
- **Requirement:** E2E: Wave spawn (2 children) + defer queue ordering + cross-system
- **Validation Command:** `python -m pytest tests/e2e/test_e2e_workflows.py::test_wave_spawn_with_defer_queue -v -m integration`
- **Status:** ⏸️ DEFERRED — Requires live daemon + LLM API keys
- **Assertion fixes:** ✅ APPLIED — The test now properly detects both P1 and P2 bugs
- **To complete:** Start daemon via `./dev.sh`, then run the test

---

## To Run the Test Live
```bash
# Terminal 1: Start daemon (requires OPENAI_API_KEY and PostgreSQL)
./dev.sh

# Terminal 2: Run the E2E test
.venv/bin/pytest tests/e2e/test_e2e_workflows.py::test_wave_spawn_with_defer_queue \
  -v -m integration --override-ini="addopts=" -s
```

---

## Overall Status
- **Assertion Fixes:** ✅ COMPLETE (Gap 1 + Gap 2)
- **Code Verification:** ✅ PASS (parse, compile, collect)
- **Live E2E Run:** ⏸️ DEFERRED (needs daemon + API keys)
- **Commit:** ✅ ecec3f01
