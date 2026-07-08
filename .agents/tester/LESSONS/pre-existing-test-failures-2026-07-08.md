# Pre-existing Test Failures (as of 2026-07-08)

## Context
During OpenSpace MCP Phase 1 regression testing, ~60 test failures were found across the full suite (~8643 tests). ALL are pre-existing — unrelated to OpenSpace MCP integration work.

## Flaky Tests (concurrency-related, pass in isolation)

### test_job_repository_atomic_transition.py
- `test_concurrent_terminal_writes_only_one_succeeds` — Passes 5/5 in isolation, fails under parallel load. Race condition in concurrent terminal writes.
- `test_concurrent_start_only_one_succeeds` — Passes in isolation, fails under load. Admission state race.

### test_atomic_dequeue.py
- `test_dequeue_concurrent_drains_n_messages_with_n_workers` — Passes 5/5 in isolation, fails under parallel load. Concurrent drain race.

## Pre-existing Failures (non-OpenSpace)

### job_queue subsystem
- Multiple failures in `test_job_repository_atomic_transition.py` — concurrent race conditions
- Various job_queue tests fail intermittently under load

### message_queue_redesign subsystem
- `test_atomic_dequeue.py` — concurrent drains race condition

### unit/services subsystem
- `test_job_queue_proxy_phase1.py::test_completed_job_mirror_overridden_by_active_instance`
- `test_jq_proxy_phase3_query_migration.py` — query migration source issues (2 tests)
- `test_jq_proxy_phase3_regression.py` — cross-cutting invariant (1 test)

### tools subsystem
- `test_send_message_status_guard.py` — status guard failure
- `test_send_message_task_repo_guard.py` — task repo guard failure

## Key Insight
When running the full test suite (`python -m pytest tests/`), concurrency-related tests fail due to parallel execution load. These pass when run in isolation or with smaller parallelism. They are NOT regressions from any specific feature work.

## Recommendation
- For regression testing, focus on specific module tests rather than full suite
- Concurrency tests should be run in isolation for reliable results
- Consider marking flaky concurrency tests with `@pytest.mark.flaky` or similar
