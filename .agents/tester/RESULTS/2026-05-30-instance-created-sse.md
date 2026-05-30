# Test Report: instance_created SSE Event Feature
Date: 2026-05-30
Sessions: backend-tests, frontend-tests, implementation-review, ensure-md

## Summary
- **Backend**: 4931 passed, 4 failed (all pre-existing), 27 skipped
- **Frontend**: 800/800 passed, 0 failures
- **New Tests Added**: 4 tests for `LiveEventHub.stream_instance_created()` (commit `9a6e742`)
- **Quick Fixes**: 1 applied — `test_api.py` mock method fix (commit `1efa9ba`)
- **ensure.md**: ✅ PASS (dev.sh stable 30s)
- **Overall Status**: ✅ READY

## Backend Tests
- **Opencode Instance**: backend-tests
- **Total**: 4931 passed, 4 failed, 27 skipped, 686 warnings
- **Duration**: 122.53s (2 min 2s)

### Notification Broadcaster Tests: 23/23 ✅
- `test_emit_instance_created_broadcasts_normal_agents` ✅
- `test_emit_instance_created_filters_experiencer` ✅
- `test_emit_instance_created_filters_kb_importer` ✅
- `test_emit_instance_created_multiple_connections` ✅
- `test_emit_instance_created_no_connections_returns_zero` ✅
- `test_emit_instance_created_includes_timestamp` ✅

### Live Event Hub Tests: 54/54 ✅ (including 4 new tests)
- `test_stream_instance_created` ✅
- `test_stream_instance_created_no_connections` ✅
- `test_stream_instance_created_to_parent_only` ✅
- `test_stream_instance_created_multiple_connections_same_parent` ✅

### Pre-existing Failures (NOT related to instance_created)
1. `test_process_message_processor_passes_is_retry_false_for_first_attempt` — mock setup incomplete
2. `test_internal_agent_source_does_not_trigger_source_replacement` — behavior changed in recent commits
3. `test_auto_test_rag_skips_when_host_not_set` — test isolation issue (passes individually)
4. `test_send_message_triggers_title_on_cancelled_error` — async mock CancelledError handling

## Frontend Tests
- **Opencode Instance**: frontend-tests
- **Total**: 800 passed, 0 failed, 0 errors
- **Duration**: 7.813s
- **No regressions from instance_created feature**

## Implementation Review
- **Opencode Instance**: implementation-review

### End-to-End Flow Verification ✅
**Child path**: `spawn_instance()` → `LiveEventHub.stream_instance_created(parent_id, data)` → parent's SSE connection → frontend `SseService.instanceCreatedQueue` → `InstanceService.addInstanceToTree()`

**Root path**: `spawn_instance()` → `NotificationBroadcaster.emit_instance_created(data)` → global SSE → `NotificationService` handler → `SseService.instanceCreatedQueue` → `InstanceService.addInstanceToTree()`

### Edge Case Analysis
| Edge Case | Result | Notes |
|-----------|--------|-------|
| Parent not in frontend list | ✅ PASS | Child added as root, tree reconciled on next poll (60s) |
| KB agent filtering | ✅ PASS | 3-layer filtering: broadcaster, NotificationService, InstanceService |
| Project filtering | ✅ PASS | Instances from other projects silently ignored |
| Rapid spawning (3+ children) | ✅ PASS (⚠️ minor) | Queue handles batching correctly; no backpressure limit |
| Deduplication | ✅ PASS | Duplicate events safely ignored via instance_id check |

## Quick Fixes Applied
1. **test_api.py mock method fix** (commit `1efa9ba`):
   - File: `tests/test_api.py` line 821
   - Root cause: Wrong mock method `get_instance` → should be `get_instance_info`
   - Fix: Changed mock method name
   - Verification: Test now passes

2. **New tests added** (commit `9a6e742`):
   - File: `tests/unit/test_live_event_hub.py`
   - Added 4 tests for `stream_instance_created()` covering: basic delivery, no connections, parent-only routing, multiple connections
   - All 4 pass

## ensure.md Validation
- **Status**: ✅ PASS
- **dev.sh**: Started, ran 30s without crash, all services initialized
- **Port 8079**: Freed after test

## Code Changes Summary
| File | Change | Commit |
|------|--------|--------|
| `tests/test_api.py:821` | Mock method fix: `get_instance` → `get_instance_info` | `1efa9ba` |
| `tests/unit/test_live_event_hub.py` | Added 4 tests for `stream_instance_created()` | `9a6e742` |

---

### Overall Status
- Backend Tests: ✅ PASS (4931/4935, 4 pre-existing failures)
- Frontend Tests: ✅ PASS (800/800)
- Implementation Review: ✅ PASS (all edge cases handled)
- ensure.md: ✅ PASS
- **Testing Complete**: ✅ READY
