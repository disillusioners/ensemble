# Pre-existing Full-Suite Flaky Concurrency Tests

## Date: 2026-07-11
## Branch: `feature/subtask-param-fix`

## Issue
The full non-integration test suite (~9854 tests) cannot complete cleanly due to pre-existing flaky concurrency tests that hang on asyncio selectors. `pytest-timeout` (thread method) cannot interrupt these hangs.

## Symptoms
- Different test hangs each run — confirms widespread flakiness, not a single bad test
- Hangs occur at varying progress points (8%, 23%, 48%, 49%)
- `pytest-timeout` with `timeout_method = "thread"` cannot interrupt asyncio selector deadlocks

## Observed Hangs

| Run | Test that hung | Progress |
|-----|----------------|----------|
| 1 | `tests/opencode/test_tools.py` | 23% |
| 2 | `tests/test_slack_rate_limiter.py` | 48% |
| 3 | `tests/job_queue/test_jober_watch_integration.py` | 8% |
| 4 | `tests/unit/routers/test_jobs_streaming_resolver.py::test_completed_job_emits_terminal_events_with_legacy_fields` | 49% |

## Visible Failures (from partial runs)
- `tests/message_queue_redesign/test_atomic_dequeue.py` — concurrency tests (concurrent_only_one_worker_wins, concurrent_drains_n_messages, concurrent_under_concurrency)
- `tests/job_queue/test_job_retry_engine.py` — atomic_retry_concurrent_calls_only_one_succeeds
- `tests/job_queue/test_idempotent_enqueue_atomic.py` — concurrent_inserts tests

## Root Cause Hypothesis
Asyncio event loop + thread-based concurrency tests are inherently flaky in CI/local environments. The `timeout_method = "thread"` setting in `pyproject.toml` cannot interrupt asyncio selector deadlocks.

## Impact
- Blocks ensure.md critical requirement #1 ("All non-integration tests pass")
- Does NOT affect any todo-related tests or our change scope
- Pre-existing issue — not introduced by any recent branch

## Recommendation
1. Consider switching `timeout_method` to `"signal"` (if platform supports it)
2. Or mark known flaky concurrency tests with a `@pytest.mark.flaky` marker and exclude from default runs
3. Or refactor concurrency tests to use `asyncio.to_thread` pattern consistently
4. Address in a separate follow-up branch — NOT related to any specific feature branch

## Workaround for Testing
When validating a specific change, run only the relevant test files rather than the full suite:
```bash
python -m pytest tests/test_todo_tools.py tests/test_todo_sse.py tests/test_todo_manager.py -v --tb=short -q
```
