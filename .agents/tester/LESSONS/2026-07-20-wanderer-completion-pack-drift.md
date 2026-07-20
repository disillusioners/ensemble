# Lesson: PACKS.md Drift + Opencode Session Caching Issue (2026-07-20)

## Context
Testing the wanderer completion-reporting bug fix (commit 8616ff45). Created 4 test packs via opencode sessions.

## Issue 1: PACKS.md Stale Paths
**Problem**: PACKS.md referenced `tests/unit/test_completion_report_idempotency.py` which does NOT exist on the `fix/wanderer-completion-reporting` branch. This file was on `feature/correlation-manager` but may not be fully merged. Also `test_cm_resilience.py`, `test_p0_stale_cleanup.py`, `test_resume_child_notification.py` (at `tests/unit/` path) don't exist (the last one is at `tests/unit/test_resume_child_notification.py` — it DOES exist, but several others don't).

**Impact**: Pack C (`completion_regression_test.sh`) failed to collect (pytest exit 4) because the missing file aborted collection before any tests ran.

**Fix**: Always verify test file paths exist BEFORE putting them in a pack script. Use `find tests -name "*pattern*"` to confirm.

**Prevention**: PACKS.md entries should be verified against the current branch before running. The drift between branches (correlation-manager vs main) means some pack entries reference files that only exist on specific feature branches.

## Issue 2: Opencode Session Caching (Stale Response)
**Problem**: Sessions C and D got "stuck" returning the same cached `latest_response` (same message ID + timestamp) even after sending new messages. `wait_for_result` returned immediately with the old result.

**Symptom**: `latest_response.info.id` and `time.completed` did NOT change across multiple `send_message` + `wait_for_result` cycles. The session state showed IDLE but the new message wasn't processed.

**Root cause (suspected)**: The session spawned a background job (session D spawned `ses_08150fbdfffe...` as a "fixer") and went IDLE waiting for a hook-driven callback. The subsequent messages didn't trigger re-processing.

**Fix**: Abort the stuck session (`external_opencode_abort_session`) and create a fresh session (`external_opencode_init_session` with a new name). The fresh session processes the message correctly.

**Prevention**: 
1. Explicitly instruct opencode sessions: "Execute DIRECTLY. Do NOT spawn background jobs."
2. If `wait_for_result` returns a response with the SAME message ID as a previous call, the session is stuck — abort and re-init.
3. Detect staleness by comparing `latest_response.info.id` across calls.

## Issue 3: Session B Forgot to Commit
**Problem**: Session B (Pack B) created `test/packs/child_reports_unit_test.sh` and ran it successfully (5/5 PASS), but reported "Quick fixes applied: None" and did NOT commit the pack script.

**Fix**: Tester committed the script directly: `git add test/packs/child_reports_unit_test.sh && git commit -m "test: add child_reports unit test pack"` (commit `444c5a48`).

**Prevention**: Always verify via `git log -- <pack_script>` that pack scripts were committed. The task template says "COMMIT REQUIRED" but sessions may skip it if they consider the script creation as non-quick-fix.
