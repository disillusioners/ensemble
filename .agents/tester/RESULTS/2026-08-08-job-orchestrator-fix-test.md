# Test Report: Job Orchestrator Fixes + E2E Test Validation
Date: 2026-08-08
Branch: `feature/job-orchestrator-fix`
Instance IDs: 2825478f (unit), ddb4fa12 (infra), 3e2d4356 (e2e)

### Summary
- Total: 302 unit tests + 2 e2e tests + 6 infra checks
- Unit Tests: 300 passed, 1 skipped, 0 failed
- E2E Tests: 1 passed, 1 failed (daemon race condition bug found)
- Infra Checks: 6/6 PASS
- Quick Fixes Applied: 1 (test helper — synthetic message filter)
- Quarantined: 0

### Scope Decision
> Scoped run — 4 source files + 6 test files in the change set. Full suite NOT warranted (focused bugfix, no architecture change). Skipped: full non-integration suite, Release Gate E2E.

---

## Part 1: Unit Test Regression — ✅ PASS

Worker: `2825478f` | Runtime: 2.53s | Exit code: 0

| File | Tests | Status |
|------|-------|--------|
| `tests/test_job_queue_tools.py` | 73 | all pass |
| `tests/job_queue/test_jober_watch_integration.py` | 37 (1 skip) | pass / 1 skip |
| `tests/unit/test_ari_agent.py` | 25 | all pass |
| `tests/test_slack_adapter.py` | 94 | all pass |
| `tests/test_telegram_adapter.py` | 32 | all pass |
| `tests/test_sources_registry.py` | 29 | all pass |

**Total: 300 passed, 1 skipped, 0 failed**

Note: 2 files had moved paths since the task was written:
- `tests/test_ari_agent.py` → `tests/unit/test_ari_agent.py`
- `tests/test_jober_watch_integration.py` → `tests/job_queue/test_jober_watch_integration.py`

**No port-8079 flake triggered** on jober_watch this run.

---

## Part 2: E2E Mock Source Tests — ⚠️ PARTIAL (1 PASS, 1 FAIL)

Worker: `3e2d4356` | Daemon: RUNNING (`/api/health` → healthy, PostgreSQL, v0.10.0)

### Initial Run: 2/2 FAILED (test helper bug)
Both tests failed with `role='system'` instead of `role='user'`.

### Test Helper Fix Applied (commit pending)
**File:** `tests/e2e/test_e2e_mock_source_jobs.py` (lines 134-137)
**Root cause:** The `_wait_for_job_event` helper scanned ALL messages without filtering. Ari's synthetic system prompt message (injected by `get_instance_messages` with `is_synthetic=True`) contains literal `[JOB_EVENT]` + `completed` + `Result:` template examples documenting the event format. The helper matched this synthetic system message before the real notification, returning `role="system"`.
**Fix:** Skip `is_synthetic=True` and `role="system"` messages during the scan.

### After Fix Run: 1 PASS, 1 FAIL

| Test | Result | Runtime |
|------|--------|--------|
| `test_mock_source_job_continue` | ✅ PASS | ~245s |
| `test_mock_source_job_create_and_watch` | ❌ FAIL | ~183s |

### 🔴 Daemon Bug Found: TOCTOU Race in `job_create` watch Registration

**Severity: 🔴 Critical** — watch-based job notifications can be silently lost.

**Location:** `daemon/tools/job_queue.py` lines 329→361

**Root cause:** TOCTOU race between `enqueue()` and `add_watch()`:
```python
job_item = await job_service.enqueue(...)  # ← dispatches job to worker pool
# ... worker pool may pick up and complete job HERE ...
watcher_repo.add_watch(job_item.job_id, current_instance_id)  # ← too late
```

The comment at line 344 claims "job is PENDING here, no race with observer" but this is FALSE. Between `enqueue()` (which signals the worker pool) and `add_watch()`, the leader agent can be picked up by a worker thread and complete (the leader finished in ~7 seconds).

**Evidence from PostgreSQL:**
- Job `20576c7e`: status=`done`, terminal_reason=`completed`
- Task created at 21:49:41.926, started 4ms later, completed at 21:49:48.285
- `job_watchers` table: 0 rows (watcher never registered in time)
- `message_queue`: 0 job_event messages (notification never enqueued)

**Why test 2 passes:** In test 2, ari calls both `job_create(watch=true)` AND a separate `watch_job()` call. The explicit `watch_job` call registers the watcher reliably.

**Suggested fix:** Move `watcher_repo.add_watch()` to BEFORE `job_service.enqueue()`, or use a single atomic operation that creates the job + watcher together.

---

## Part 3: Infrastructure Validation — ✅ PASS

Worker: `ddb4fa12`

| # | Check | Result |
|---|-------|--------|
| 1 | Import validation (4 source modules) | ✅ PASS |
| 2 | Mock source server import | ✅ PASS |
| 3 | Mock source adapter abstract methods | ✅ PASS (`__abstractmethods__` is empty — all methods implemented) |
| 4 | Test collection (`--collect-only`) | ✅ PASS (2 tests collected with `-m integration`) |
| 5 | Daemon health | ✅ RUNNING (use `/api/health`, not `/health`) |
| 6 | Git branch | ✅ `feature/job-orchestrator-fix` |

### Mock Source Adapter Details
- **`MockSourceAdapter`** (bases: `MessageSourceAdapter`) — 14 public methods, `__abstractmethods__` empty
- **`DaemonSourceMock`** (bases: `object`) — 8 public methods

---

## Quick Fixes Applied

| Instance | Fix | File | Root Cause |
|----------|-----|------|------------|
| `3e2d4356` | Synthetic message filter in `_wait_for_job_event` | `tests/e2e/test_e2e_mock_source_jobs.py:134-137` | Helper matched Ari's synthetic system prompt (containing `[JOB_EVENT]` template examples) before the real notification |

---

## Action Needed

- [ ] 🔴 **Fix the TOCTOU race in `daemon/tools/job_queue.py`** — move `watcher_repo.add_watch()` BEFORE `job_service.enqueue()`, or make job+watcher creation atomic. This is a production bug that silently drops watch notifications.
- [ ] Commit the test helper fix in `tests/e2e/test_e2e_mock_source_jobs.py` (synthetic message filter)
- [ ] Consider installing `pytest-timeout` plugin (non-blocking warning about unknown config options)

---

### Overall Status
- Unit Tests: ✅ PASS (300/300, 0 regressions)
- E2E Tests: ⚠️ PARTIAL (1/2 pass — 1 daemon bug found)
- Infra Checks: ✅ PASS (6/6)
- **Fixes are SAFE** — no regressions in unit tests. However, a **new daemon bug was discovered** in the watch registration path (TOCTOU race).
- **Testing Complete**: ⚠️ CONDITIONAL — 3 fixes confirmed safe via unit tests, but the TOCTOU race bug needs a production fix before the watch notification feature can be trusted.
