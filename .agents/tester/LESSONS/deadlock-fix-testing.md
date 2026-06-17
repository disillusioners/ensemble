# Deadlock Fix Testing — Lessons Learned

## Test Typo in test_deadlock_fix.py

**Issue**: The test `test_waiting_children_sse_emits_with_correct_agent_id` had a duplicated assertion block (20 lines) that referenced an undefined variable `waiting_children_call`. The actual variable was `wc_call`.

**Root Cause**: Copy-paste error during test creation — duplicate block was both broken (wrong variable name) AND redundant (real assertions existed earlier).

**Fix**: Deleted the duplicate block (commit 597ef93f).

**Lesson**: When tests have long assertion blocks, verify variable names are consistent throughout. The NameError only surfaced at runtime, not at import time.

---

## Pre-existing Test Failures (41 tests)

**Pattern**: Many tests fail with "no such table: projects" and "coroutine was never awaited" errors.

**Affected files**: test_manager.py, test_progressive_dispatch.py, test_spawn_limit_edge_cases.py

**Root Cause**: Test fixtures don't create the `projects` table. `InstanceManager` calls `stream_status_change` synchronously via `MainLoopBridge.run_async_no_wait` and the mock returns a non-awaitable.

**Note**: These failures exist on the base branch and are NOT caused by the deadlock fix.

---

## Port 8079 Conflict

**Issue**: The test `test_ensure_dev_sh_still_works` fails because port 8079 is occupied.

**Root Cause**: Multiple processes holding the port during test runs.

**Fix**: Kill processes before running: `lsof -ti:8079 | xargs kill` (per AGENTS.md, do NOT use pkill -f uvicorn).

---

## Deadlock Fix Verification Strategy

**What worked well**:
1. Splitting verification into source code analysis + test execution (parallel)
2. Thread-identity test pattern (`_spy_thread`/`_spied_to_thread`) effectively validates asyncio.to_thread wrapping
3. Source analysis caught the intentional C1 TOCTOU invariant exception (atomic_transition NOT wrapped by design)

**Out-of-scope finding**: Additional sync DB calls exist in instance_lifecycle.py, migration_worker.py, job_retry_engine.py, dead_letter_service.py that should be audited in a follow-up.
