# Quarantined Tests

Pre-existing failures that are skipped and do NOT count toward a pack's PASS/FAIL.
These exist in the repo before the current change and are unrelated to it.

## Active

| Test | Pack / File | Date Quarantined | Reason | Retry Budget | Attempts (P/F) | Status |
|------|-------------|------------------|--------|--------------|----------------|--------|
| TestManagerGetInstanceAsync::test_manager_get_instance_delegates_to_lifecycle_service | tests/unit/test_mcp_cold_load_race.py (spawn_mcp_preload_gap_test) | 2026-08-14 | Pre-existing: `MigrationError: Migration 20260714_000001 failed` — SQLite `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS` syntax error. Known dual-driver migration issue (migration landed in 2b77c4cd, predates PM domain-access; same failure class as RESULTS/2026-08-10 report). NOT a PM-change regression. | 1 (attribution via git diff, not flake) | 1F | QUARANTINED (skip-markered) |

## Resolved (history)

| Test | Pack | Date Resolved | Fix | Confirming Runs |
|------|------|---------------|-----|-----------------|
| _none yet_ | | | | |
