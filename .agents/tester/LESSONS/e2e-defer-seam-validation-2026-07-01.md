# E2E Defer Seam Bugfix Validation — Lessons Learned

**Date**: 2026-07-01  
**Session**: e2e-defer-queue-validation  
**Context**: Running 4 E2E tests to validate 17-bug defer queue + job/task seam fix

## Results
All 4 E2E tests PASSED on first run (no quick fixes needed):
- test_parent_child_workflow_happy_path: 48s PASS
- test_pause_after_spawn_then_resume: 46s PASS
- test_terminate_after_spawn_then_revive: 41s PASS
- test_wave_spawn_with_defer_queue: 81s PASS

## Key Environment Gotchas

### 1. SSL_CERT_FILE stale env var
**Issue**: Shell environment had `SSL_CERT_FILE` set pointing to a deleted venv cert, causing httpx SSL errors when the daemon tried to make LLM API calls.
**Fix**: Start daemon with `env -u SSL_CERT_FILE -u SSL_CERT_DIR ./dev.sh` to unset stale env vars.
**Lesson**: If daemon startup fails with SSL errors, check for stale cert env vars.

### 2. data_dev/ensemble.json database must be postgres
**Issue**: E2E tests require PostgreSQL (the project's primary DB). The dev data dir's ensemble.json was set to sqlite.
**Fix**: Changed database field from `sqlite` to `postgres` in data_dev/ensemble.json.
**Warning**: This was left on postgres after testing. If dev environment needs sqlite, revert manually.

### 3. RAG_IS_REQUIRED must be false during E2E
**Issue**: E2E tests don't set up RAG infrastructure, causing daemon startup failures if RAG_IS_REQUIRED=true.
**Fix**: Temporarily set to false in .env, restored to true after.

### 4. __pycache__ cleanup before testing
**Issue**: Stale .pyc files can trigger daemon reloads mid-test (known issue from previous sessions).
**Fix**: `find . -type d -name __pycache__ -exec rm -rf {} +` before starting daemon.

## Assertion Strengthening Validation
The strengthened assertions in commit `ecec3f01` worked correctly:
- **P1 coverage**: Requiring positive terminal state (`completed`) catches stuck-in-processing
- **P2 coverage**: Periodic defer-job status sampling catches premature completion
- All P1/P2 bugfixes confirmed working — no regressions
