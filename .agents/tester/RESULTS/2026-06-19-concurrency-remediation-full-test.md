# Test Report: Concurrency Remediation — Full Integration Test
Date: 2026-06-19
Branch: feature/concurrency-fixes (8 commits fixing 54 concurrency findings)
Sessions: concurrency-full-suite, full-suite-clean, concurrency-code-review, concurrency-migrations, concurrency-edge-cases, pre-existing-verify, ensure-validation

## Summary
- **Total: 8021** | Passed: 7891 | Failed: 73 | Errors: 0 | Skipped: 27 | XFailed: 5 | Deselected: 4
- **Pre-existing failures: 46** (verified on `latest` branch)
- **New failures from concurrency fixes: ~27** (net delta)
- **Migrations: ALL PASS** on fresh SQLite (42 migrations, including the fixed 000002)
- **Concurrency test quality: 3/5 categories have real race-condition tests, 2/5 are weak**
- **Edge cases: ALL PASS** (rowcount==0, version_id_col, backward compat)
- **ensure.md: 2/4 Critical PASS, 2/4 Critical FAIL**

---

## 1. Full Test Suite Results (CLEAN BRANCH — no unauthorized modifications)

### Per-Directory Breakdown

| Directory | Passed | Failed | Errors | Skipped | XFailed |
|---|---|---|---|---|---|
| tests/unit/ | 3204 | 24 | 0 | 0 | 0 |
| tests/job_queue/ | 1349 | 2 | 0 | 19 | 0 |
| tests/repositories/ | 148 | 0 | 0 | 0 | 0 |
| tests/opencode/ | 469 | 0 | 0 | 0 | 0 |
| tests/services/ | 38 | 0 | 0 | 0 | 0 |
| tests/tools/ | 55 | 0 | 0 | 0 | 0 |
| tests/message_queue_redesign/ | 398 | 5 | 0 | 0 | 1 |
| tests/test_*.py (root) | 2221 | 42 | 0 | 8 | 5 |
| tests/api/ | 9 | 0 | 0 | 0 | 0 |
| **TOTAL** | **7891** | **73** | **0** | **27** | **5** |

---

## 2. Failure Classification: Pre-Existing vs. Caused by Concurrency Fixes

### Pre-Existing Failures (46 total — verified on `latest` branch)

These failures exist on `latest` BEFORE the concurrency fixes. They are NOT caused by this branch.

| Cluster | Tests | Root Cause | Pre-Existing? |
|---|---|---|---|
| **A. Missing DB schema in fixtures** | ~28 | `sqlite3.OperationalError: no such table: projects/instances` — test fixtures don't run `create_all` | ✅ YES |
| **B. ExecutionGate MagicMock incompatibility** | 9 | `TypeError: '>' not supported between MagicMock and float` at execution_gate.py:274 | ✅ YES |
| **C. Config/constants drift** | 3 | `DEFAULT_PAGE_LIMIT=10` (test expects 20), `max_instance_history=500` (test expects 300), api.py 718 lines (test expects <700) | ✅ YES |
| **D. Innate skills prompt text** | 3 | `'OpenCode_Skill' in prompt` fails for coder agent | ✅ YES |
| **E. Stale recovery retry** | 4 | `assert retry_task is not None` — retry scheduling for CANCELLED tasks | ✅ YES (partially — see below) |
| **F. test_exponential_backoff_calculation** | 1 | `assert retry1 is not None` → None returned | ✅ YES |
| **G. Concurrency tests on latest** | ~2 | test_task_lock_manager (2 fail), test_job_repository_atomic_transition (1 fail) | ✅ YES |

### New Failures from Concurrency Fixes (~27 net delta)

These failures are NEW — they exist on `feature/concurrency-fixes` but NOT on `latest`.

| Cluster | Tests | Root Cause | Severity |
|---|---|---|---|
| **H. Tree-aware pause/resume cascade** | 18 | `test_tree_aware_pause_resume.py` — cascade no longer calls `instance_repository.update` for children. Tests PASS on latest, FAIL on our branch. | **HIGH** — feature regression |
| **I. Related directories guard** | 2 | New ValueError in project/repository.py:480 blocks legacy `update(related_directories=...)` | MEDIUM — test migration needed |
| **J. Concurrent acquire InterfaceError** | 1 | `sqlite3.InterfaceError: bad parameter or other API misuse` in concurrent acquire test | MEDIUM — test harness issue |
| **K. Paused instance TTL** | 1 | `test_paused_instance_ttl.py` — update() not called. PASS on latest, FAIL on branch. | MEDIUM — related to Cluster H |
| **L. test_exponential_backoff worsening** | 0-3 | The stale_recovery retry failures MAY have worsened (some pre-existing, some new) | LOW — unclear |

**Critical finding**: The **tree-aware pause/resume cascade** (Cluster H, 18 tests) is the biggest regression. These tests PASS on `latest` and FAIL on our branch, meaning the concurrency fixes broke this functionality.

---

## 3. Concurrency Test Quality Assessment

### Do the tests ACTUALLY verify race conditions?

| Category | Real Race Tests? | Mechanism | Verdict |
|---|---|---|---|
| **1. Status Transitions** | ✅ YES | `threading.Barrier(2)` + `threading.Thread` (5+ tests) | **REAL** — verifies loser's data not written |
| **2. Retry Dedup** | ✅ YES | `threading.Barrier(n)` + `ThreadPoolExecutor` + file-backed SQLite/WAL | **STRONGEST** — true cross-thread with real DB |
| **3. Lock Lifecycle** | ⚠️ MIXED | `asyncio.gather` for in-process; sequential for slot-claim; `patch.object` for exception paths | **WEAK on races, STRONG on exception coverage** |
| **4. JSON/JSONB Atomicity** | ✅ YES | `threading.Barrier(16)` + `threading.Thread` (8+ tests, 16-thread fanout) | **REAL** — 16 threads, all keys survive |
| **5. Transaction Boundaries** | ❌ MOSTLY NO | Sequential `patch.object` mid-cascade failure injection; ONE `asyncio.gather` | **WEAK on races, STRONG on rollback** |

### Key Coverage Gaps Identified
1. **No concurrent `terminate_instance` test** — two threads racing on same instance untested
2. **`try_acquire_slot` cross-process race tested sequentially only** — the C5 fix's UNIQUE constraint is verified by serial calls, not concurrent threads
3. **Startup sweep has no concurrent-acquire race** — `recover_stale_job_locks` not tested running mid-acquire
4. **Several "concurrent" test names are misleading** — they patch in pre-seeded state and call once (sequential)
5. **`inspect.getsource` tests verify code shape, not runtime behavior** — they'd pass on broken try/finally as long as the words appear

---

## 4. Migration Test Results

### ALL 42 Migrations PASS on Fresh SQLite ✅

- Migration `000002` (version columns): **PASS** — the semicolon-in-comment bug is FIXED by commit `b7895c05`
- Migrations `000003` through `000006` (unique constraints, version columns): **ALL PASS**
- Idempotency verified: 2nd and 3rd runs apply 0 migrations
- Schema verification: 27 tables, 91 indexes (8 UNIQUE), all critical columns present

### Migration Issue (Non-Blocking)
- File `20260619_120000_fix_idempotency_index_include_deleted_at.sql` is **silently skipped** — missing `-- UP` section header
- Impact: Benign on fresh DBs (SQLModel.metadata.create_all creates the correct index before migrations run)
- Impact on existing DBs: If old non-partial index exists, this migration won't fix it
- Recommendation: Add `-- UP` marker to the file

---

## 5. Edge Case Test Results

### ALL EDGE CASES PASS ✅

**1. rowcount == 0 Handling (13 atomic UPDATEs)**
- All properly handle `rowcount==0`: either raise (race detected) or return None (row missing)
- Live SQLite test confirms `rowcount=0` on no-match, `rowcount=1` on match

**2. version_id_col Optimistic Locking**
- Correctly configured on Task and JobItem models
- Live test confirms `StaleDataError` raises on stale write
- Version auto-increments correctly
- ORM UPDATE emits `WHERE job_id=? AND version=?`

**3. Backward Compatibility**
- No broken callers — all public return type contracts preserved
- Critical callers explicitly handle None: `job_retry_engine.py`, `job_queue_service.py`, `worker_pool.py`

**Documentation-only finding**: JobItem model comment overstates version_id_col coverage for Core UPDATE path (status guards provide actual race safety there)

---

## 6. ensure.md Validation Results

### Critical Requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | All non-integration tests pass | ❌ **FAIL** | 73 failures (46 pre-existing, ~27 new) |
| 2 | Deadlock fix tests pass | ✅ **PASS** | 11/11 tests pass |
| 3 | No sync DB calls on event loop | ✅ **PASS** | 323 `asyncio.to_thread` usages; all DB calls wrapped |
| 4 | dev.sh has --timeout-graceful-shutdown 10 | ✅ **PASS** | Both comment and command line present |

### Important Requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Async callers use await | ✅ **PASS** | All 13 callers of `_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats` use `await` |
| 2 | Deadlock scenario works | ✅ **PASS** | Deadlock fix tests validate parent→child→complete flow |

### Nice-to-have

| # | Requirement | Status |
|---|---|---|
| 1 | No dead code | ✅ **PASS** — no broken imports detected |

### Summary: Critical 2/4, Important 2/2, Nice-to-have 1/1

---

## 7. Quick Fixes Applied

**No quick fixes were authorized for this test run.** This was a measurement/verification task.

**NOTE**: One session (full-suite-v2) made unauthorized modifications to 10 files (446 insertions). These were detected via `git status` and immediately reverted with `git checkout -- .`. All results from that session were discarded and re-run cleanly. Lesson documented in LESSONS/unauthorized-modifications-by-sessions.md.

---

## Action Needed

- [ ] **HIGH PRIORITY**: Fix tree-aware pause/resume cascade regression (18 tests failing — Cluster H)
- [ ] **MEDIUM**: Migrate `related_directories` tests to new API or adjust guard (Cluster I)
- [ ] **MEDIUM**: Investigate concurrent acquire InterfaceError (Cluster J)
- [ ] **LOW**: Add missing concurrent tests: terminate_instance race, try_acquire_slot cross-process, startup sweep race
- [ ] **LOW**: Fix migration file `20260619_120000` missing `-- UP` header
- [ ] **LOW**: Rename misleading "concurrent" test names that are actually sequential

---

## Overall Status

| Area | Status |
|---|---|
| Full Test Suite | ❌ FAIL (73 failures: 46 pre-existing + ~27 new) |
| Concurrency Test Quality | ⚠️ PARTIAL (3/5 categories strong, 2/5 weak) |
| Migrations | ✅ PASS (all 42 apply cleanly) |
| Edge Cases | ✅ PASS (rowcount, version_id, backward compat) |
| ensure.md | ⚠️ PARTIAL (2/4 critical pass, but critical #1 fails) |

**Testing Complete**: ❌ NOT READY — 18-test regression in tree-aware pause/resume must be fixed before merge
