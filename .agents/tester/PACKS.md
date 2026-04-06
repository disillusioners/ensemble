# Test Packs

## Summary
- Total: 4 packs
- Unit: 1 | Integration: 1 | Job Queue: 1 | Top-level: 1

## Test Packs

| Pack | Location | Scope | Type | Last Run | Status |
|------|----------|-------|------|----------|--------|
| unit_test | tests/unit/ | Unit tests (compaction, find_near, graph_retry, idle_timeout, llm_error_classifier, response_validation) | unit | - | PENDING |
| integration_test | tests/integration/ | Integration tests (agent_bootstrap, compaction_e2e, completion_report, inner_soul, instance_title, message_queue, multi_turn_resume, sse_streaming, streaming_errors, streaming_performance) | integration | - | PENDING |
| job_queue_test | tests/job_queue/ | Job queue tests (task_lock_manager, task_queue_integration, task_queue_repository, task_queue_service) | unit | - | PENDING |
| toplevel_test | tests/test_*.py | Top-level unit tests (agents_api, api, cancellation, config, events, help_tool, instance_title, loader, manager, memory_system, migration, models, persistence, project_store, project_tools, queue, registry, scheduler, sources, spawn_instance, telegram_adapter, tools) | unit | - | PENDING |

---

## Pack Details

### unit_test
- **Path**: `tests/unit/`
- **Tests**: 172
- **Timeout**: 2 minutes
- **Script**: `test/packs/unit_test.sh`

### integration_test
- **Path**: `tests/integration/`
- **Tests**: 69
- **Timeout**: 5 minutes
- **Script**: `test/packs/integration_test.sh`

### job_queue_test
- **Path**: `tests/job_queue/`
- **Tests**: 150
- **Timeout**: 2 minutes
- **Script**: `test/packs/job_queue_test.sh`

### toplevel_test
- **Path**: `tests/test_*.py`
- **Tests**: 910
- **Timeout**: 5 minutes
- **Script**: `test/packs/toplevel_test.sh`

## Updating PACKS.md

Update after each test run:
- **Last Run**: timestamp
- **Status**: PASS/FAIL/TIMEOUT
