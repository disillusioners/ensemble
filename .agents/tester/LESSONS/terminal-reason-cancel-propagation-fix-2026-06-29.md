# terminal_reason Cancel Propagation Fix — Verification Lesson

**Date**: 2026-06-29
**Commit**: `3b9ade2b`
**Session**: `e2e-terminal-reason-verify`

## Problem Solved

Previously, cancelled jobs surfaced as `'completed'` in `/api/work/{id}` because `_job_to_record()` used `Instance.status` as the sole source of truth. When a job was cancelled, the Instance status could still report `completed`/`terminated`, masking the cancellation.

## The Fix

Commit `3b9ade2b` adds a `terminal_reason` column to `job_queue_items` that records HOW a job terminated (e.g., `cancelled`, `completed`, `failed`). `_job_to_record()` now checks `terminal_reason` FIRST, before falling back to `Instance.status`.

**Priority order**: `terminal_reason` → `admission_state` → `Instance.status`

## Verification Evidence

Database row after cancel:
```
admission_state = done
terminal_reason = cancelled
```

Work surface response:
```
status = 'cancelled'  (was 'completed' before fix)
```

All 4 E2E tests pass on PostgreSQL (191s total runtime).

## Key Pattern: Status Derivation Priority

When a job has multiple status signals (admission_state, terminal_reason, Instance.status), the derivation must follow a priority chain:
1. **terminal_reason** — most specific, records the actual termination cause
2. **admission_state** — coarse-grained (queued/active/done/dead)
3. **Instance.status** — fallback only

This prevents cancelled/failed jobs from being masked by a stale or misleading Instance.status.

## Setup Gotcha: PostgreSQL Auto-Detect Fallback

The `data_dev/ensemble.json` config file can override PostgreSQL auto-detection. Even when `POSTGRES_HOST` and `POSTGRES_DB` are set, if `ensemble.json` says `{"database": "sqlite"}` and an `instances.db` file exists, the daemon will use SQLite. Must explicitly set `{"database": "postgres"}` for E2E tests on PostgreSQL.
