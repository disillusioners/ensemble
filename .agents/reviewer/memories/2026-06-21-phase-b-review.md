# Phase B Review (commits bad3bea3 + 3ae8a72e) — 2026-06-21

## Verdict: REQUEST CHANGES (1 critical, 2 warnings, 1 suggestion)

The live path (watch_job → register → job terminal → pre-fetch → resolve_job → parent completes) is correct and closes Variant B. But crash recovery is silently broken by a one-character attribute name typo.

## 🔴 CRITICAL

### B-F1 — Attribute name typo disables crash recovery for watched-job parents
- `daemon/api.py:249`: `manager._watcher_repo = watcher_repo` (SETTER)
- `daemon/services/correlation_manager.py:1794`: `getattr(manager, "_watcher_repository", None)` (READER — different name!)
- Result: `self._watcher_repo` is ALWAYS None → `rebuild_from_db` Step 5 (`if self._watcher_repo is not None:`) is DEAD CODE
- Impact: After crash/restart, a parent whose only pending work is a watched job in PROCESSING has empty `pending_jobs` → premature completion → orphan. Variant B survives a crash.
- Fix: One character — `_watcher_repository` → `_watcher_repo`

## ✅ Verified Correct (live path)

| Area | Status |
|------|--------|
| B1 is_complete (all 4 combinations) | ✅ Correct |
| B2 resolve_job mirrors resolve_response (H7, W1, terminal_status) | ✅ Correct |
| B2 generation counter bumps (register_job_send + resolve_job) | ✅ Correct |
| B3 watch_job wiring (parent_id, DB-first, terminal guard) | ✅ Correct |
| B4 pre-fetch ordering (pre-fetch → notify_watchers → notify_corr_resolve_job) | ✅ Correct |
| get_watched_processing_job_ids query (JOIN, filters, deleted_at) | ✅ Correct |
| rebuild_from_db Step 5 logic (MERGE, lock, watcher_repo failure) | ✅ Correct (but dead code due to F1) |
| clear_for_instance (wipes pending_jobs) | ✅ Correct |
| rearm_parent (empty ParentCorrelation) | ✅ Correct |

## 🟡 Warnings

### B-W1 — resolve_job generation bump → spurious re-arm cycles
- resolve_job bumps gen outside the lock. If a resolve lands during _finalize_job's window, post_gen > pre_gen → spurious COMPLETED→PROCESSING→COMPLETED cycle.
- Self-correcting (second cycle fires outbox correctly). Cost: DB/SSE flicker, one event-loop iteration delay on terminal signal.
- Fix: Document in comment. Acceptable as-is.

### B-W2 — H7 restore with concurrent register loses had_error
- If callback raises AND concurrent register lands in the microsecond window between lock release and H7 restore, the H7 restore sees _pending already populated → skips → had_error from the errored resolve is silently discarded → terminal_status becomes "completed" instead of "error".
- Narrow trigger (requires callback exception + concurrent register + error status). Low probability.
- Fix: Maintain `self._last_terminal_had_error[parent_id]` dict, or MERGE had_error into concurrent register's entry.

### B-W3 — Test coverage gaps (B3, B4, rebuild Step 5 untested)
- All 10 tests are CM-level unit tests (register/resolve directly). No test exercises:
  - B3 wiring (watch_job → notify_corr_register_job)
  - B4 ordering (pre-fetch before notify_watchers) — this is the MOST critical path and has no regression guard
  - rebuild_from_db Step 5
- The attribute typo (F1) went undetected because no test exercises the wiring.

## 🟢 Suggestions

### B-S1 — Duplicate assignment in __init__
- `correlation_manager.py:145-148`: `self._watcher_repo = watcher_repository` assigned twice. Delete one.
