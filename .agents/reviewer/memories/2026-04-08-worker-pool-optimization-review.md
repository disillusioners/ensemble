# Worker Pool Optimization Review (cff91b1)

## Summary
Reviewed hybrid notification + backup polling pattern replacing continuous DB polling.
Found 2 critical bugs (one prevents startup), several warnings, insufficient test coverage.

## Critical Findings
1. **manager.py:503** — `poll_interval` parameter removed from WorkerPool but still passed → TypeError at runtime
2. **repository.py:468,666** — `_notify_pending_task()` called inside transaction (before commit) → workers may try to claim uncommitted tasks

## Key Observations
- `notify_all()` + count=1 means only 1 worker decrements counter; N-1 workers misclassified as "timeout"
- Exception handler in Worker.run() uses old-style `_stop_event.wait()` instead of `wait_for_work()`, missing notifications during error recovery
- `_stats` dict has inconsistent lock discipline (some inside lock, some outside)
- MockWorkerPool duplicated 3 times across test files
- Zero tests verify the actual notification mechanism works (notify → wake → claim)
