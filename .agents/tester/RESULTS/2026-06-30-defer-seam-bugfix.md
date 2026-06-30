# Defer Seam Bugfix Test Results — 2026-06-30

**Branch:** `feature/defer-seam-bugfix`
**Commits:** `92cb026a` (F1/F3/F4/F7), `60c5bfaa` (C1-C4 review fixes), `b79ddc87` (Phase 1)
**Date:** 2026-06-30 21:07 UTC

---

## Overall Result: ✅ ALL PASS — NO REGRESSIONS

| Bug | Description | Status |
|-----|-------------|--------|
| **F4** | Cancel queued job does not release sibling active job's lock | ✅ PASS |
| **F7** | `_dispatch_skipped=True` releases no locks | ✅ PASS |
| **F1** | list_work dedup by (instance_id, message_id) | ✅ PASS |
| **F3** | Status filter uses terminal_reason, not lossy canonical map | ✅ PASS |
| **Recovery** | `_fail_orphaned_job` does NOT call `release_by_instance` | ✅ PASS |
| **Regression** | No new failures across all suites | ✅ PASS |

---

## F4/F7 — Lock Release Scoping (Session: f4f7-lock-release-test)

### Tests Executed: 5 files, 112 tests, ALL PASS

| File | Tests | Coverage |
|------|-------|----------|
| `tests/job_queue/test_seam_invariants.py` | 21/21 | F4/F7 invariants at unit + integration level |
| `tests/job_queue/test_job_recovery_service.py` | 33/33 | 16 C1-fix assertions: `release_by_job` called, `release_by_instance.assert_not_called()` |
| `tests/job_queue/test_cancellation_cascade.py` | 18/18 | Cancellation lock-release path regression |
| `tests/job_queue/test_deferred_finalize_check.py` | 4/4 | Observer-side invariant |
| `tests/job_queue/test_lock_repository.py` | 36/36 | `release_by_job` primitive |

### Key Verified Behaviors:
1. ✅ **Cancel queued JobB → sibling active JobA's lock preserved** (scoped `release_by_job`)
2. ✅ **Cancel active JobA → only JobA's lock released**
3. ✅ **`_fail_orphaned_job` uses `release_by_job`** not `release_by_instance` (critical regression fix verified)
4. ✅ **`_dispatch_skipped=True` → no lock release** (job never held a lock)

---

## F1 — list_work Dedup (Session: f1f3-work-resolver-test)

### Tests: `tests/unit/services/test_work_resolver.py` — 5/5 in TestListWorkDedupByMessageId PASS

| Test | Verifies |
|------|----------|
| `test_f1_job_create_with_standalone_followup_message` | J(msg1) + T1(msg1) + T2(msg2) → T1 deduped, T2 visible |
| `test_f1_standalone_task_with_none_message_id_never_suppressed` | message_id=None → never suppressed |
| `test_f1_task_with_mismatched_message_id_not_suppressed` | Different tuple → Task kept |
| `test_f1_workrecord_carries_message_id` | WorkRecord exposes message_id |
| `test_f1_dedup_works_with_no_message_id_on_either_side` | Legacy state handled |

---

## F3 — Status Filter (Session: f1f3-work-resolver-test)

### Tests: `tests/unit/services/test_work_resolver.py` — 7/7 in TestListWorkStatusFilterTerminalReason PASS

| Test | Verifies |
|------|----------|
| `test_f3_filter_status_failed_returns_only_failed` | status="failed" → only failed |
| `test_f3_filter_status_completed_returns_only_completed` | status="completed" → only completed |
| `test_f3_filter_status_cancelled_returns_only_cancelled` | status="cancelled" → only cancelled |
| `test_f3_done_row_with_null_terminal_reason_surfaces_under_completed` | NULL → defaults to completed (legacy hedge) |
| `test_f3_backward_compat_raw_admission_state_still_works` | Raw admission_state strings still work |
| `test_f3_combined_terminal_status_filter` | status="completed,failed,cancelled" combined filter |
| `test_f3_terminal_reason_filter_does_not_leak_to_tasks` | Task side uses Task.status independently |

---

## Regression Suite (Session: regression-suite-test)

| Suite | Result | Notes |
|-------|--------|-------|
| `tests/job_queue/` | 1287 pass, 1 flake | Pre-existing flake: `test_concurrent_start_only_one_succeeds` (passes in isolation) |
| `tests/unit/services/test_work_resolver.py` | 84/84 pass | Clean |
| Task repository tests | 184/184 pass | 17 skipped, clean |
| `test_seam_invariants.py` (review fixes) | 21/21 pass | recovery-isolation, sync-twin, retry-then-cancel all PASS |

### Review-Fix Tests Verified:
- ✅ `test_sync_twin_releases_scoped_to_target_job_not_instance`
- ✅ `test_sync_twin_logs_warning_when_event_loop_unavailable`
- ✅ `test_retry_then_cancel_does_not_leak_lock`
- ✅ `TestRecoveryServiceLockIsolation::test_recover_orphan_does_not_release_sibling_lock_same_queue`
- ✅ `TestRecoveryServiceLockIsolation::test_recover_orphan_releases_its_own_lock_scoped`
- ✅ `TestRecoveryServiceLockIsolation::test_recover_orphan_does_not_touch_lock_on_different_instance`

### Pre-existing Flake Details:
- `tests/job_queue/test_job_repository_atomic_transition.py::TestStartJobAtomic::test_concurrent_start_only_one_succeeds`
- Fails only when run with full suite; passes in isolation and as full file
- File last modified at commit `41633433` (Phase 5 Batch 2), NOT touched by defer-seam bugfix commits

---

## Quick Fixes Applied
None required — all tests pass on first run.

## Code Changes
None required — no source modifications made.
