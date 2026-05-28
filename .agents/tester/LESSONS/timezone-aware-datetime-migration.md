# Timezone Display Fix Testing (2026-05-28)

## What Changed
Branch `fix/timezone-display` replaced `datetime.utcnow()` → `datetime.now(timezone.utc)` in 15 backend files. This makes `.isoformat()` output timezone-aware strings ending with `+00:00`.

## Key Finding: Naive vs Aware Datetime Comparison
- Python 3 raises `TypeError` when comparing timezone-aware and naive datetimes
- This caused test failures in `test_idempotent_enqueue.py` where TTL comparison silently failed
- **Pattern**: When production code changes to timezone-aware, test mocks must also use timezone-aware datetimes
- **Fix**: Import `timezone` and use `datetime.now(timezone.utc)` instead of `datetime.utcnow()` in test helpers

## Quick Fix Applied
- File: `tests/job_queue/test_idempotent_enqueue.py`
- 6 lines changed (4 test functions + import + helper)
- Commit: `0cf872a`

## Verdict
- 0 new regressions from timezone change
- Dev server stable (30s)
- All timestamp format properties verified
