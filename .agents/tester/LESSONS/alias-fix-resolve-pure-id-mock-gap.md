# Alias Fix Regression — job_queue_service.resolve_pure_id() Mock Gap

**Date**: 2026-06-25
**Branch**: `feature/rename-coder-to-developer` @ `daba08ac`
**Severity**: Medium (4 test regressions, no production impact)

## Problem

The C2 fix in `daemon/services/job_queue_service.py` adds a `registry.resolve_pure_id()` call before using `agent_id` in `enqueue()`. This is correct production code.

However, existing tests in `tests/job_queue/test_message_job_queue.py` mock `registry.get()` but do NOT mock `registry.resolve_pure_id()`. When the code calls the unmocked method, it returns a `MagicMock` object, which then gets passed as `agent_id` into a SQL INSERT statement. SQLite rejects this with:

```
sqlalchemy.exc.ProgrammingError: (sqlite3.ProgrammingError) 
Error binding parameter 2: type 'MagicMock' is not supported
```

## Affected Tests (4)

1. `TestHttpMessageJobQueuePath::test_http_message_routes_to_parallel_queue`
2. `TestHttpMessageJobQueuePath::test_http_message_full_flow`
3. `TestNoProjectContext::test_message_job_no_project_routes_to_system_parallel`
4. `TestNoProjectContext::test_message_job_default_project_queue_type`

## Pattern: When Adding resolve_pure_id() Calls

**Rule**: Any test that mocks `registry.get()` must ALSO mock `registry.resolve_pure_id()` when the code path under test calls the new alias resolution method.

The pattern:
```python
# In test fixtures, add:
mock_registry.resolve_pure_id.return_value = "developer"  # or expected agent_id
# Already exists:
mock_registry.get.return_value = mock_agent_def
```

## Quick Fix Path

Update 4 test fixtures in `tests/job_queue/test_message_job_queue.py` to mock `resolve_pure_id()`. < 20 lines, single file, obvious root cause.
