# Test Report: OpenCode Session Manager Resource Leak Fixes
Date: 2026-06-11
Branch: `feature/opencode-session-resource-guard`
Commits: `f86064b` → `bb28a85` → `f9f0013` (test fix)

## Summary
- **Unit Tests**: 461/461 PASS (opencode suite)
- **Keyword Tests**: 646/646 PASS (after pre-existing fix, 2 deselected)
- **New Tests**: 14/14 PASS (all resource leak tests)
- **ensure.md**: ✅ PASS (dev.sh stable for 30s)
- **Quick Fixes**: 1 (pre-existing gaia agent test, commit f9f0013)
- **Status**: ✅ READY

## Resource Leak Fixes Validated

### Fix 1: abort_session() pops manager from _managers and stops it (P0)
- **Tests**: `TestAbortSessionResourceLeak` (2 tests)
  - ✅ `test_abort_session_removes_manager_from_memory` — manager popped from dict
  - ✅ `test_abort_session_keeps_db_row` — repository row survives for reload
- **Code**: `registry.py` abort_session now pops from `_managers` under lock, calls `stop()`, DB row intact

### Fix 2: create_new() stops old manager before creating replacement (P0)
- **Tests**: `TestCreateNewStopsOldManager` (3 tests)
  - ✅ `test_create_new_stops_old_manager` — old_manager.stop() awaited
  - ✅ `test_create_new_pops_old_manager_from_memory` — old manager removed from dict
  - ✅ `test_create_new_tolerates_old_manager_stop_failure` — failure logged, creation proceeds
- **Code**: `registry.py` create_new pops old manager before remote abort

### Fix 3: _run_loop() idle guard — IDLE sessions use 5-min heartbeat (P1)
- **Tests**: `TestIdleHeartbeat` (2 tests)
  - ✅ `test_idle_heartbeat_uses_longer_interval` — IDLE sleeps 300s (not 30s)
  - ✅ `test_active_session_uses_poll_interval` — BUSY still uses 30s
- **Code**: `session_manager.py` _run_loop checks `is_idle` under lock, sleeps `IDLE_HEARTBEAT_S`

### Fix 4: evict_idle_sessions() — TTL-based eviction (P1)
- **Tests**: `TestEvictIdleSessions` (3 tests)
  - ✅ `test_evict_idle_sessions_removes_expired` — expired manager evicted and stopped
  - ✅ `test_evict_idle_sessions_keeps_active` — active manager preserved
  - ✅ `test_evict_idle_sessions_tolerates_stop_failure` — stop error logged, eviction proceeds
- **Code**: `registry.py` new method with 1h default TTL, wired into `_cleanup_cached_instances()`

### Fix 5: _touch_activity() — only updates on meaningful interactions (P1)
- **Tests**: `TestPollDoesNotTouchActivity` (2 tests)
  - ✅ `test_touch_activity_not_called_in_poll` — _last_activity unchanged after poll
  - ✅ `test_poll_questions_does_not_call_touch_activity` — spy confirms no invocation
- **Code**: `session_manager.py` new `_touch_activity()` method, called from submit/abort/get_status only

### Fix 6: Double-stop safety (P2)
- **Tests**: `TestStopSafety` (2+ tests)
  - ✅ `test_double_stop_is_safe` — triple stop() no crash
  - ✅ `test_stop_without_start_is_safe` — stop before start is no-op
- **Code**: `session_manager.py` stop() is idempotent

## Quick Fixes Applied
- **f9f0013**: Fixed pre-existing `test_gaia_agent.py` assertions — `meta.json` has 5 tools (includes `"context"`), tests expected 4
  - Root cause: `agents/gaia/meta.json` allow list was updated but tests weren't
  - Fix: Updated 2 test assertions to include `"context"`
  - Unrelated to resource leak branch, discovered during keyword test run

## ensure.md Validation
- ✅ PASS — dev.sh ran for full 30 seconds without crash (exit code 124)
- Server initialized: PostgreSQL, OpenCode registry, RAG, WorkerPool, MCP
- Clean shutdown, no lingering processes

## Test Execution Details

### Suite 1: `tests/opencode/` (full suite)
| Metric | Value |
|--------|-------|
| Total | 461 |
| Passed | 461 |
| Failed | 0 |
| Errors | 0 |
| Skipped | 0 |
| Time | 10.57s |

### Suite 2: Keyword tests (`ttl or opencode or session_manager or registry`)
| Metric | Value |
|--------|-------|
| Total run | 646 |
| Passed | 646 |
| Failed | 0 (after fix) |
| Skipped | 2 |
| Deselected | 5854 |
| Time | 19.76s |

## Code Changes Summary
- `daemon/opencode/session_manager.py` — _touch_activity(), last_activity property, idle heartbeat, double-stop safety
- `daemon/opencode/registry.py` — evict_idle_sessions(), abort pops+stops manager, create_new stops old manager
- `daemon/opencode/constants.py` — IDLE_HEARTBEAT_S = 300
- `daemon/manager.py` — eviction wired into _cleanup_cached_instances()
- `tests/opencode/test_session_manager.py` — 6 new tests (PollActivity, IdleHeartbeat, StopSafety)
- `tests/opencode/test_registry.py` — 8 new tests (AbortResourceLeak, CreateNewStops, EvictIdle)
- `tests/unit/test_gaia_agent.py` — 2 assertion fixes (pre-existing)

---

### Overall Status
- Unit Tests: ✅ PASS (461/461 + 646/646 keyword)
- ensure.md: ✅ PASS (dev.sh stable 30s)
- **Testing Complete**: ✅ READY — All resource leak fixes validated, all tests green
