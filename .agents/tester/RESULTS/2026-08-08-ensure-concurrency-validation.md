# ensure.md Concurrency Validation — 2026-08-08 (re-run)

## Scope
Re-validate Core Critical requirement for the watchover feature work on `feature/watchover`:

1. Deadlock / concurrency integrity — `concurrency_atomic_unit_test`.
2. No synchronous DB calls on the asyncio event loop — covered by the deadlock/thread-identity tests in the same mapped pack.

## Pack Resolution
`PACKS.md` maps `concurrency_atomic_unit_test` to:

- `tests/test_cascade_concurrency.py`
- `tests/test_cascade_race3.py`
- `tests/test_deadlock_fix.py`
- `tests/test_instance_delete_by_project_locking.py`
- `tests/test_instance_metadata_atomic.py`
- `tests/test_observer_race1.py`
- `tests/test_project_repository_atomic.py`

No shell pack script exists under `test/packs/`, so the exact PACKS.md-mapped test set was run directly using `.venv/bin/pytest`, wrapped with `timeout 300`.

## Result
Command:

```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble && \
  timeout 300 .venv/bin/pytest \
    tests/test_cascade_concurrency.py \
    tests/test_cascade_race3.py \
    tests/test_deadlock_fix.py \
    tests/test_instance_delete_by_project_locking.py \
    tests/test_instance_metadata_atomic.py \
    tests/test_observer_race1.py \
    tests/test_project_repository_atomic.py \
    --tb=short
```

- **PASS: 66 passed, 19 skipped, 0 failed**
- Collected: 85 tests
- Duration: 6.23 seconds
- Skips: 9 cascade + 7 cascade-race + 3 observer-race (CM-era skips on SQLite); no tests are listed in `QUARANTINE.md`.
- Per-file outcomes:
  - `test_cascade_concurrency.py`: 10 skipped (CM-era)
  - `test_cascade_race3.py`: 7 skipped (CM-era)
  - `test_deadlock_fix.py`: 10 passed (includes `asyncio.to_thread` and thread-identity checks)
  - `test_instance_delete_by_project_locking.py`: 9 passed
  - `test_instance_metadata_atomic.py`: 15 passed
  - `test_observer_race1.py`: 3 skipped (CM-era)
  - `test_project_repository_atomic.py`: 32 passed

## Critical Requirement Status
- ✅ **Deadlock / concurrency integrity**: PASS — 66 passed, 19 skipped, 0 failed.
- ✅ **No sync DB calls on asyncio event loop**: PASS — `tests/test_deadlock_fix.py` thread-identity assertions (`test_finalize_db_sync_runs_off_loop_thread`, `test_process_child_completion_db_sync_runs_off_loop_thread`, `test_send_error_report_db_sync_runs_off_loop_thread`) and `asyncio.to_thread` scheduling spies all passed.

## Thread-Identity Coverage (Requirement #2)
The pack proves no sync DB call runs on the asyncio event loop via thread-identity tests in `tests/test_deadlock_fix.py`:

| Test | Assertion |
|------|-----------|
| `test_finalize_db_sync_runs_off_loop_thread` | `_finalize_db_sync` thread identity differs from asyncio loop thread |
| `test_finalize_db_sync_is_scheduled_via_to_thread` | `_finalize_instance` calls `asyncio.to_thread(_finalize_db_sync, ...)` |
| `test_process_child_completion_db_sync_runs_off_loop_thread` | `_process_child_completion_db_sync` thread identity differs from asyncio loop thread |
| `test_process_child_completion_db_sync_is_scheduled_via_to_thread` | `notify_watchers` calls `asyncio.to_thread(...)` to schedule the sync DB helper |
| `test_send_error_report_db_sync_runs_off_loop_thread` | `_send_error_report_db_sync` thread identity differs from asyncio loop thread |
| `test_send_error_report_db_sync_is_scheduled_via_to_thread` | `_send_error_report` schedules via `asyncio.to_thread(...)` |

## Warnings
Pytest emitted two existing `PytestConfigWarning` messages because the installed environment lacks the `timeout` plugin (`timeout` and `timeout_method` are unknown config options in `pyproject.toml`). The required outer `timeout 300` wrapper was applied successfully and the suite completed well under the 5-min cap. No quick fixes were applied.

## Cross-Reference
This run reproduces the result recorded in `RESULTS/2026-08-07-ensure-concurrency-validation.md`. Outcome is stable across the intervening commits on `feature/watchover` (latest HEAD `1240cfd7`).
