# Test Report: All Packs First Run
Date: 2026-04-07

## Summary
- **Total packs**: 7 (4 PASS, 3 FAIL, 0 TIMEOUT)
- **Total tests**: ~1285 (1148 passed, ~15 failed, ~11 skipped)
- **All within timeout** — no TIMEOUT issues

## Pack Results

### ✅ core_unit_test — PASS
- 644 passed, 0 failed | 14.07s

### ❌ api_unit_test — FAIL (8 pre-existing)
- 147 passed, 8 failed | 5.42s
- All 8 failures in `test_spawn_instance_instructive_errors.py`
- **Root cause**: Feature never implemented on this branch — code returns generic `"Agent not found: X"` instead of instructive error messages
- **Status**: Pre-existing, known from previous test runs

### ✅ sources_unit_test — PASS
- 111 passed, 0 failed

### ✅ compaction_unit_test — PASS
- 177 passed, 0 failed

### ❌ job_queue_unit_test — FAIL (1 flaky)
- 147 passed, 1 failed, 2 skipped
- `test_concurrent_enqueue_same_project` — SQLite concurrency issue (bad parameter / API misuse)
- **Status**: Known regression from asyncio.to_thread + SQLite in-memory (noted in README)

### ❌ integration_test — FAIL (6 failures)
- 56 passed, 6 failed, 7 skipped (no OPENAI_API_KEY — expected)
- 3 inner_soul tests: `Agent not found: test_agent`
- 1 title generation: missing event broadcast
- 3 message_queue: `sqlite3.OperationalError: disk I/O error` (DB contention)

### ❌ mock_job_queue_test — FAIL (1 flaky, same as job_queue)
- 147 passed, 1 failed, 2 skipped
- Same `test_concurrent_enqueue_same_project` failure (runs same job_queue tests)

## Known Issues
1. **Instructive errors (8 tests)**: Pre-existing — feature not implemented on this branch
2. **SQLite concurrency (2 packs, same test)**: Known regression — needs `StaticPool` fix
3. **Integration test failures (6 tests)**: Agent registration + SQLite contention issues

## Action Items
- [ ] SQLite StaticPool fix for job_queue concurrent tests (affects 2 packs)
- [ ] Investigate integration test agent registration failures
