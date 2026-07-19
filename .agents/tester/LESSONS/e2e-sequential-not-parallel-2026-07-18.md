# E2E Tests Must Run Sequentially — Not in Parallel

**Date**: 2026-07-18
**Reference**: ensure.md Release Gate E2E tests

## Root Cause

E2E tests from `tests/e2e/test_e2e_workflows.py` share the **same daemon instance** at `localhost:8079` and the **same job queue**. Running them in parallel causes cross-test interference:

- Tests create instances, spawn children, and enqueue jobs that persist in the shared queue
- A test that creates deferred jobs leaves them in `pending` state
- The next test's defer queue admission logic sees these stale jobs and blocks, causing **false failures**

## Evidence

During the 2026-07-18 run:
- All 4 E2E tests were launched in parallel (4 opencode sessions simultaneously)
- Tests 1-3 passed, but test 4 (`test_wave_spawn_with_defer_queue`) **failed initially** because 4 stale active/paused jobs (left by the parallel runs) blocked the defer queue
- The deferred job remained `pending` for 120s before timing out
- After cleaning the stale jobs and re-running, test 4 **passed** (64s)

## Correct Approach

1. **Run E2E tests one-by-one (sequential)** — never parallel
2. **Clean the job queue before EVERY test**:
   - Check: `curl -s "http://localhost:8079/api/jobs?status=pending"`
   - Also check processing/active jobs
   - Cancel/delete all stale jobs before running
3. **Wait for each test to fully complete** before launching the next

## Queue Cleanup Command

```bash
# Check for stale jobs
curl -s "http://localhost:8079/api/jobs?status=pending"
curl -s "http://localhost:8079/api/jobs?status=processing"

# Clean up (cancel each stale job by ID)
curl -s -X DELETE "http://localhost:8079/api/jobs/{job_id}"
# Or use cleanup endpoint if available
curl -s -X POST "http://localhost:8079/api/jobs/cleanup"
```

## Impact

- **Before (parallel)**: Test 4 false failure due to queue interference, required re-run
- **After (sequential + cleanup)**: All 4 tests pass cleanly on first attempt
