# Phase A: Test-Fixture Mock Issue (MagicMock > int TypeError)

**Date**: 2026-06-20
**Category**: Test Fixture Issue (NOT Production Regression)
**Files**: message_job_handler.py, message_processing_pipeline.py, 10 test files

## Problem

Phase A's `on_success` callback in `daemon/services/message_job_handler.py:480-616` performs:
```python
wf = getattr(instance, "waiting_for", None) or 0
if wf > 0:
    skip_complete = True
```

When tests use `MagicMock()` for the manager and instance objects:
- `instance.waiting_for` returns a MagicMock (truthy)
- `MagicMock() > 0` raises `TypeError` in Python 3.14 (comparison not supported)
- The TypeError is silently swallowed by `message_processing_pipeline.py:498-504` as "(non-fatal)"
- `complete_job` is never called → test assertions fail

## Why Production is Safe

In real production:
1. `get_correlation_manager()` returns a real CM (initialized at API startup)
2. `cm.get_pending_count(instance_id)` returns an actual `int`
3. `wf > 0` comparison works correctly
4. The legacy path is unreachable when CM is wired (CM branch taken first)

## Affected Tests (10)

| File | Test |
|------|------|
| tests/unit/test_dispatch_completed_fix.py | test_job_still_completes_after_dispatch |
| tests/unit/test_dispatch_completed_fix.py | test_dispatch_error_does_not_fail_job |
| tests/unit/test_dispatch_completed_fix.py | test_dispatch_error_propagates_not_to_job_completion |
| tests/unit/test_dispatch_completed_fix.py | test_handler_does_not_crash_without_source_dispatcher |
| tests/unit/test_dispatch_completed_fix.py | test_handler_does_not_crash_with_source_but_no_dispatcher |
| tests/job_queue/test_pause_while_processing.py | test_normal_completion_still_works |
| tests/message_queue_redesign/test_message_flow.py | test_completion_handler_called_after_successful_processing |
| tests/message_queue_redesign/test_message_flow.py | test_completion_handler_called_when_message_id_is_none |
| tests/message_queue_redesign/test_message_flow.py | test_completion_handler_error_does_not_fail_job |
| tests/message_queue_redesign/test_message_flow.py | test_completion_handler_not_called_when_manager_lacks_method |

## Fix Approach

Mock `get_correlation_manager()` in test fixtures to return a CM with `get_pending_count=lambda _i: 0`.

Or alternatively: set `manager.config.job_system.use_legacy_waiting_for_cascade = False` AND provide a CM mock.

## Related Pattern

The same `MagicMock > int` TypeError pattern affects `test_spawn_limit_edge_cases.py` (9 tests) via `execution_gate.py:274`.
