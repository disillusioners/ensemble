# Quarantined Tests

Pre-existing failures that are skipped and do NOT count toward a pack's PASS/FAIL.
These exist in the repo before the current change and are unrelated to it.

## Active

| Test | Pack / File | Date Quarantined | Reason | Retry Budget | Attempts (P/F) | Status |
|------|-------------|------------------|--------|--------------|----------------|--------|
| test_pause_during_report_turn_then_resume_closes_orphan_path | tests/e2e/test_pause_during_report_turn_then_resume.py (Release Gate 1e) | 2026-08-20 | Pre-existing stale assertion: expects message_queue 'completed' at resume; semantics shifted PAUSED→CANCELLED→PAUSED→PENDING in c171a289 (2026-08-13); test file unchanged since 4e82c8c9 (2026-08-02, pre-c171a289). Fails identically on latest. D-2 base-evidence 2026-08-20: FAILED identically on base 6bb99d5f with `assert 'processing' == 'completed'` at line 700 (test files byte-identical between base and HEAD; c171a289 IS ancestor of base — failures predate this branch). KEEP quarantined (deterministic pre-existing stale assert). | 2 (1 branch + 1 base, deterministic) | 1F @ HEAD 706c44a1; 1F @ base 6bb99d5f | QUARANTINED (deterministic stale assert, base-evidenced) |
| test_resume_after_pause_during_report_consumes_handle | tests/e2e/test_pause_during_report_resume_turn_handle.py (Release Gate 1f) | 2026-08-20 | Pre-existing stale assertion: "ResumeTurn must transition PAUSED → CANCELLED; got 'pending'" — c171a289 (2026-08-13) semantic shift (PAUSED→PENDING for WorkerPool re-claim). Test file unchanged since 4e82c8c9 (2026-08-02). D-2 base-evidence 2026-08-20: FAILED identically on base 6bb99d5f with `assert 'pending' == 'cancelled'` at line 530. Diff-independent — keep quarantined. | 2 (1 branch + 1 base, deterministic) | 1F @ HEAD 706c44a1; 1F @ base 6bb99d5f | QUARANTINED (deterministic stale assert, base-evidenced) |
| test_resume_does_not_create_new_task_or_jobitem | tests/e2e/test_pause_during_report_resume_turn_handle.py (Release Gate 1f) | 2026-08-20 | Pre-existing stale assertion: JobItem 'active' vs expected 'done' — c171a289 (2026-08-13) shift (reconciler no longer force-completes message; deferred finalize path owns it). Test file unchanged since 4e82c8c9 (2026-08-02). D-2 base-evidence 2026-08-20: FAILED identically on base 6bb99d5f with `assert 'active' == 'done'` at line 651. Diff-independent — keep quarantined. | 2 (1 branch + 1 base, deterministic) | 1F @ HEAD 706c44a1; 1F @ base 6bb99d5f | QUARANTINED (deterministic stale assert, base-evidenced) |
| TestManagerGetInstanceAsync::test_manager_get_instance_delegates_to_lifecycle_service | tests/unit/test_mcp_cold_load_race.py (spawn_mcp_preload_gap_test) | 2026-08-14 | Pre-existing: `MigrationError: Migration 20260714_000001 failed` — SQLite `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS` syntax error. Known dual-driver migration issue (migration landed in 2b77c4cd, predates PM domain-access; same failure class as RESULTS/2026-08-10 report). NOT a PM-change regression. | 1 (attribution via git diff, not flake) | 1F | QUARANTINED (skip-markered) |

## Resolved (history)

| Test | Pack | Date Resolved | Fix | Confirming Runs |
|------|------|---------------|-----|-----------------|
| _none yet_ | | | | |
