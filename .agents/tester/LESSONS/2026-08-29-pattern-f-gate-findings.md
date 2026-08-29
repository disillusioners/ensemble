# Pattern (f) Gate Findings — W4 characterization commit + residual pair (2026-08-29)

## 1. c16b21e8 is TEST-ONLY despite "fix(...)" message
Red-green worktree verification: both `TestDeferDoubleEmitIdempotency` tests are GREEN at c16b21e8's parent (dee03665). `git diff dee03665..c16b21e8` = +194 test-file lines, zero production. The `WHERE watch_id AND state='PENDING'` guard already existed (repository.py:608; landed with the earlier emit work in this batch). **W4 was resolved by characterization coverage, not a behavior change** — the exactly-once behavior was instead gate-proven independently by the defer_bus_emit probe (P1, real-DB called-twice via the dispatch path).
Follow-up 🟢: relabel/annotate; the mock-based test is self-fulfilling on its side_effect lists but is honestly documented and paired with `test_transition_state_real_db_idempotency_via_guard` (real DB — verified).

## 2. Residual pair shares one root cause — fix as a pair (~3 LOC)
- `has_inflight_task` (repository.py:468-541) = PENDING+RUNNING only; sister `has_instance_busy` (:543) adds PAUSED. Pattern (f) lineage gate uses the narrow one → PAUSED retry children invisible (edge: pause after force_cancel_and_schedule_retry → parent finalized → retry child stranded; resume W1 guard :4739-4744 excludes it; terminate-only recovery).
- Lineage-lookup `except Exception` (job_recovery_service.py:3296-3312) fails toward FINALIZE — opposite of the sister bus gate (:3138-3153 "FAIL-SAFE: skip... Never guess"); a transient DB error can over-finalize and trigger the (a) deadlock.
- **Paired fix:** `has_instance_busy` swap @ :3297 + exception-path return True @ :3312 + regression tests (extend pattern_f_killpath_matrix_test.py scenario (b) with a PAUSED retry child; transient-error simulation in test_w1_retry_child_lineage_conjunct.py). Both 🟠 non-blocking for THIS merge (narrow windows, recovery paths exist, council non-block stands).

## 3. Pattern (f) dead-writes (🟢)
f1/f2 pass `completed_at`/`error_message` kwargs to `atomic_transition`, which silently strips them (`_REMOVED_JOB_COLUMNS`, repository.py:47-54/:1260-1268 — documented). `terminal_reason` on the f1/f2 DEAD/DONE path defers to the finalize seam (documented :2755-2764). Not defects; diagnostics-visibility cleanup candidate.

## 4. Reusable gate assets
- Kill-path per-leg mutation pattern (only-leg-blocking scenario × {unmutated skip-label, mutated wrongful-finalize}) — proves guard load-bearing + scenario lethality; reusable for any sweep/gate code.
- Capstone "honest wedge" method: verify what ACTUALLY blocks the queued job before claiming the sweep unblocks it (here: defer idle gate, not the lock).
