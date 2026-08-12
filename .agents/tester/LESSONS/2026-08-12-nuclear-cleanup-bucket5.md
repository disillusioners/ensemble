# LESSON: Nuclear System Cleanup Bucket 5 — Instance Zombie Reaper Testing

Date: 2026-08-12
Branch: `feature/nuclear-system-cleanup` @ `8a717b91`

## What was tested

The Bucket 5 feature adds an instance-level zombie reaper to `cleanup_non_terminal_jobs()`. It finds non-terminal instances with no live JobItems and no live Tasks, then terminates them.

## Key findings

### Implementation is solid
- **Defensive coding**: per-zombie `try/except` + outer `try/except` (two layers of error isolation)
- **Race-safe**: `transition_status_if` returns `None` if another path already flipped status → correctly not counted
- **Correct ordering**: Bucket 5 runs AFTER Buckets 1–4, so instances whose work was just cancelled get re-evaluated
- **`total_processed` invariant preserved**: `terminated_instances` is excluded (2-bucket contract holds)

### SQL portability confirmed
The `_build_zombie_scan_sql()` builder bakes string literals into the SQL (terminal statuses, live task statuses, live JobItem states) instead of using SQLAlchemy's `expanding` parameter style. This was done for cross-dialect portability. Our PG parity test confirmed: zero portability issues on PostgreSQL.

### Test coverage approach
- **SQLite unit tests** (38 tests in `test_jobs_cleanup_endpoint.py`): mock-based service logic + real SQLite StaticPool for SQL
- **E2E integration test** (12 scenarios, new file): real SQLite with real repositories, only mocks for graph task / cascade
- **PostgreSQL parity** (9 tests, new file): confirms anti-join SQL works on PG

## Pre-existing failure: terminate+revive E2E

The Release Gate `test_terminate_after_spawn_then_revive` failed. Root cause: known Task↔JobItem reconciliation gap (JobItem done/cancelled but Task stays paused → blocks idle-gates). The revived leader gets stuck in `waiting_children`. This is documented in critical notes and is NOT a regression from Bucket 5.

## Test files created
- `tests/integration/test_nuclear_cleanup_bucket5.py` — 12 scenarios
- `tests/postgres/test_nuclear_cleanup_zombie_pg.py` — 9 PG parity tests
