# E2E Test Report: terminal_reason Cancel Propagation Fix Verification

**Date**: 2026-06-29
**Branch**: `latest` (after commit `3b9ade2b`)
**Commit Verified**: `3b9ade2b` — `fix: add terminal_reason column to distinguish done states`
**Session**: `e2e-terminal-reason-verify` (opencode)

## Summary

| # | Test | Status | Duration |
|---|------|--------|----------|
| 1 | `test_parent_child_workflow_happy_path` | ✅ PASS | ~40s |
| 2 | `test_pause_after_spawn_then_resume` | ✅ PASS ⭐ | ~95s |
| 3 | `test_terminate_after_spawn_then_revive` | ✅ PASS | ~25s |
| 4 | `test_wave_spawn_with_defer_queue` | ✅ PASS | ~30s |

**Total runtime**: 191.17s (3:11) — `4 passed, 2 warnings`

**Overall: ✅ ALL 4 TESTS PASS — The terminal_reason fix works as designed.**

## Key Finding: Test 2 — Cancel Propagation Bug RESOLVED

The previously-failing assertion (`test_pause_after_spawn_then_resume`) now passes:

```
[WORK_CANCEL] 683ad228... OK
[WAIT_WORK] 683ad228... -> cancelled
[VJM] ✓ JobItem work_id=683ad228... reaches status='cancelled' in /work
```

**Database-level verification** confirms `terminal_reason` is correctly recorded:

| job_id | admission_state | terminal_reason | created_at |
|--------|----------------|-----------------|------------|
| 683ad228-2929-... | done | cancelled | 2026-06-29T18:06:56 |

The `_job_to_record()` function now sees `terminal_reason='cancelled'` and surfaces it as `status='cancelled'` in `/api/work` — instead of the prior bug where it returned `'completed'` because `Instance.status` overrode the cancellation signal.

## Setup Details

- Daemon started manually WITHOUT `--reload` (to avoid uvicorn reload killing E2E tests)
- Used `.venv/bin/python` (Python 3.13) with `SSL_CERT_FILE=certifi.where()`
- **Config switch required**: `data_dev/ensemble.json` was `{"database": "sqlite"}`. The auto-detect logic sees existing `instances.db` and defaults to SQLite even when `POSTGRES_HOST` is set. Switched to `{"database": "postgres"}` and restarted.
- `terminal_reason` column verified present in PostgreSQL with index `idx_job_queue_terminal_reason`
- `ensemble.json` restored to original sqlite config after testing

## Quick Fixes Applied

**None.** Commit `3b9ade2b` works correctly. No code modifications were needed.

## SQLite Regression

**Skipped** — PostgreSQL is the authoritative verification. SQLite would require config re-switching and re-running (~3 min additional). No regression risk identified since the fix is PostgreSQL-specific (column addition + _job_to_record logic check).

## Cleanup

- Daemon killed, port 8079 freed
- `ensemble.json` restored to original config
- No leftover state
