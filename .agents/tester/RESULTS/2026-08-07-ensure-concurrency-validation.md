# ensure.md Concurrency Validation — 2026-08-07

## Scope
Core Critical requirements validated for the pause/resume graph behavior change:

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
timeout 300 .venv/bin/pytest \
  tests/test_cascade_concurrency.py \
  tests/test_cascade_race3.py \
  tests/test_deadlock_fix.py \
  tests/test_instance_delete_by_project_locking.py \
  tests/test_instance_metadata_atomic.py \
  tests/test_observer_race1.py \
  tests/test_project_repository_atomic.py \
  -v --tb=short -q
```

- **PASS: 66 passed, 19 skipped, 0 failed**
- Collected: 85 tests
- Duration: 6.54 seconds
- Skips: cascade/observer CM-era tests; no tests are listed in `QUARANTINE.md`.
- `test_deadlock_fix.py`: 10 passed, including `asyncio.to_thread` and thread-identity checks for synchronous DB helper offloading.
- Cascade concurrency: 9 skipped (CM-era)
- Cascade race: 7 skipped (CM-era)
- Observer race: 3 skipped (CM-era)
- Instance delete locking: 9 passed
- Instance metadata atomicity: 15 passed
- Project repository atomicity: 32 passed

## Critical Requirement Status
- ✅ Deadlock / concurrency integrity: PASS — 66 passed, 19 skipped, 0 failed.
- ✅ No sync DB calls on asyncio event loop: PASS — `test_deadlock_fix.py` thread-identity and `asyncio.to_thread` assertions passed.

## Warnings
Pytest emitted two existing `PytestConfigWarning` messages because the installed environment lacks the `timeout` plugin (`timeout` and `timeout_method` are unknown config options). The required outer `timeout 300` wrapper was applied successfully. No quick fixes were needed.
