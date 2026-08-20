# Quarantined Tests

Pre-existing failures that are skipped and do NOT count toward a pack's PASS/FAIL.
These exist in the repo before the current change and are unrelated to it.

## Active

| Test | Pack / File | Date Quarantined | Reason | Retry Budget | Attempts (P/F) | Status |
|------|-------------|------------------|--------|--------------|----------------|--------|
| test_pause_during_report_turn_then_resume_closes_orphan_path | tests/e2e/test_pause_during_report_turn_then_resume.py (Release Gate 1e) | 2026-08-20 | Pre-existing stale assertion: expects message_queue 'completed' at resume; semantics shifted PAUSED→CANCELLED→PAUSED→PENDING in c171a289 (2026-08-12, pre-branch base 6bb99d5f); test file unchanged since e8ff8861 (2026-08-01). Fails identically on latest. Needs assertion update for post-Phase-4b/4c semantics (worker pool + _finalize_job_db_sync complete later). | 1 (git-history attribution, deterministic) | 1F | QUARANTINED (deterministic stale assert) |
| test_resume_after_pause_during_report_consumes_handle | tests/e2e/test_pause_during_report_resume_turn_handle.py (Release Gate 1f) | 2026-08-20 | Pre-existing stale assertion: "ResumeTurn must transition PAUSED → CANCELLED; got 'pending'" — same c171a289 semantic shift as above. | 1 (git-history attribution, deterministic) | 1F | QUARANTINED (deterministic stale assert) |
| test_resume_does_not_create_new_task_or_jobitem | tests/e2e/test_pause_during_report_resume_turn_handle.py (Release Gate 1f) | 2026-08-20 | Pre-existing stale assertion: JobItem 'active' vs expected 'done' — same c171a289 shift (reconciler no longer force-completes message; deferred finalize path owns it). | 1 (git-history attribution, deterministic) | 1F | QUARANTINED (deterministic stale assert) |
| TestManagerGetInstanceAsync::test_manager_get_instance_delegates_to_lifecycle_service | tests/unit/test_mcp_cold_load_race.py (spawn_mcp_preload_gap_test) | 2026-08-14 | Pre-existing: `MigrationError: Migration 20260714_000001 failed` — SQLite `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS` syntax error. Known dual-driver migration issue (migration landed in 2b77c4cd, predates PM domain-access; same failure class as RESULTS/2026-08-10 report). NOT a PM-change regression. | 1 (attribution via git diff, not flake) | 1F | QUARANTINED (skip-markered) |

## Resolved (history)

| Test | Pack | Date Resolved | Fix | Confirming Runs |
|------|------|---------------|-----|-----------------|
| _none yet_ | | | | |
