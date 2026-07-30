# Test Report: VS Code Reliability Fixes (3 fixes across 2 commits)

**Date:** 2026-07-29
**Branch:** `feature/vscode-reliability-fixes`
**Commits:** `6629a435` (WS proxy guard) + `fe159eb3` (auto-restart + crash visibility)
**Quick Fix Commit:** `81e83473` (user_stopped guard in restart loop — found during testing)

---

## Summary

| Metric | Value |
|--------|-------|
| Total packs run | 5 |
| Total tests | **154** |
| Passed | **154** |
| Failed | **0** |
| Errors | **0** |
| Timeout | **0** |
| Quick fixes applied | **1** (source bug found + fixed + 2 new tests) |
| Overall status | ✅ **READY** |

---

## Scope Decision

> **Full suite NOT run.** The change touches 4 production files within the VS Code editor subsystem (`vscode_proxy.py`, `vscode_server_manager.py`, `schemas.py`, `settings.py`) — reliability hardening, no architecture change. Blast radius is **MODERATE/SCOPED**. Ran the 5 directly-affected VS Code packs in parallel. Skipped: all non-VSCode packs (core_unit_test, api_unit_test, frontend, etc.). Full suite not warranted.

---

## Per-Pack Results

### 1. vscode_server_manager_unit_test — ✅ PASS (61/61, 3.32s)
- **Worker:** `vscode-mgr-unit` (d79a1ab7)
- **Pack:** `tests/unit/test_vscode_server_manager.py`
- **Quick fix applied:** See Quick Fixes section below.

| Fix | Scenario | Test(s) | Status |
|-----|----------|---------|--------|
| Auto-restart | Watchdog attempts restart on crash | `test_watchdog_auto_restarts_on_crash` | ✅ |
| Backoff | Exponential backoff (1s, 2s, 4s...) | `test_watchdog_backoff_doubles_per_attempt` | ✅ |
| Max attempts | Status="crashed" after exhaustion (5) | `test_watchdog_marks_crashed_after_max_attempts` | ✅ |
| Reset | Successful restart → "running" | `test_watchdog_successful_restart_after_crash` | ✅ |
| User stop | user_stopped=True prevents restart | `test_watchdog_does_not_restart_when_user_stopped` | ✅ |
| Stale reset | port/pid/pgid reset before new start | `test_start_resets_stale_port_on_auto_restart` | ✅ |
| Crash visibility | log_buffer tail → last_error | `test_watchdog_crash_reason_surfaces_log_tail` + 8 `_wait_for_port_` tests | ✅ |
| Edge: empty log | Empty log_buffer clean message | `test_wait_for_port_empty_log_buffer_clean_message` | ✅ |
| Edge: non-UTF8 | Corrupt bytes → errors="replace" | `test_watchdog_crash_log_tail_handles_non_utf8_bytes` | ✅ (NEW) |
| Edge: first-try success | Restart succeeds on first try | `test_watchdog_auto_restarts_on_crash` | ✅ |
| Edge: all fail | Restart fails all attempts | `test_watchdog_marks_crashed_after_max_attempts` | ✅ |
| Edge: user stops mid-backoff | Stop during restart backoff | `test_watchdog_user_stopped_during_restart_backoff` | ✅ (NEW) |

### 2. vscode_proxy_integration_test — ✅ PASS (41/41, ~1s)
- **Worker:** `vscode-proxy-integ` (38f0b330)
- **Pack:** `tests/integration/test_vscode_proxy.py`

| Scenario | Test | Status |
|----------|------|--------|
| upstream_to_browser breaks when browser WS closed | `test_upstream_to_browser_breaks_when_ws_state_not_connected` | ✅ |
| RuntimeError caught by except* clause | 3 variants (helper, send, send_text) | ✅ |
| Proxy closes both sides when upstream dies | `test_proxy_closes_both_sides_when_upstream_dies` | ✅ |

### 3. vscode_editor_settings_api_test — ✅ PASS (37/37, 1.11s)
- **Worker:** `vscode-editor-api` (78aa5c92)
- **Pack:** `tests/api/test_editor_settings.py`

| Scenario | Test | Status |
|----------|------|--------|
| VSCodeStatus schema has last_error + exit_code | `test_vscode_status_schema_declares_crash_fields` | ✅ |
| _build_vscode_status populates crash fields | `test_build_vscode_status_populates_crash_fields_from_state` | ✅ |
| Returns None when no crash | `test_build_vscode_status_returns_none_when_no_crash` | ✅ |
| None when no manager | `test_build_vscode_status_none_when_no_manager` | ✅ |
| API endpoint includes crash fields | `test_editor_endpoint_includes_crash_fields` | ✅ |
| No API regressions | 32 pre-existing tests | ✅ |

### 4. vscode_lifecycle_integration_test — ✅ PASS (7/7, ~5.5s)
- **Worker:** `vscode-lifecycle-integ` (e72a9718)
- **Pack:** `tests/integration/test_vscode_lifecycle_integration.py`

| Scenario | Test | Status |
|----------|------|--------|
| Editor switch vscode→start | `test_put_vscode_starts_server_and_persists` | ✅ |
| Editor switch builtin→stop | `test_put_builtin_stops_running_server_and_persists` | ✅ |
| W13: VSCodeServerError → 503 + not persisted | `test_server_error_returns_503_and_does_not_persist` | ✅ |
| W13: NotInstalled → 503 + not persisted | `test_binary_not_installed_returns_503_and_does_not_persist` | ✅ |
| Crash recovery: dead process → "crashed" | `test_auto_restart_exhaustion_marks_crashed` | ✅ |
| Auto-restart on crash with backoff | `test_dead_subprocess_triggers_auto_restart` | ✅ |
| Status surfaces "running" after restart | `test_status_endpoint_surfaces_running_after_auto_restart` | ✅ (NEW) |

### 5. vscode_security_integration_test — ✅ PASS (8/8, ~2.2s)
- **Worker:** `vscode-security-integ` (738f0d6c)
- **Pack:** `tests/integration/test_vscode_security_integration.py`

| Scenario | Test | Status |
|----------|------|--------|
| C1: /etc rejected (403) | `test_c1_etc_root_rejected` | ✅ |
| C1: /etc/passwd rejected (403) | `test_c1_etc_passwd_rejected` | ✅ |
| C1: ../../etc rejected (403) | `test_c1_relative_traversal_rejected` | ✅ |
| C1: null byte rejected (403) | `test_c1_null_byte_injection_rejected` | ✅ |
| C1: valid repo folder not blocked | `test_c1_valid_repo_folder_not_blocked` | ✅ |
| C4: no port/pid leak (3 endpoints) | 3 endpoint tests | ✅ |

---

## Quick Fixes Applied

### Bug: watchdog restart loop ignored user_stopped during inter-attempt backoff
- **Commit:** `81e83473`
- **Root cause:** The watchdog's restart `for`-loop did not re-check `state.user_stopped` between attempts. The `user_stopped` guard only existed at the outer `while`-loop (line 965). Calling `stop()` during an inter-attempt backoff still ran all 5 retries — violating the "user_stopped=True prevents restart" contract.
- **Fix:** 14 lines in `daemon/services/vscode_server_manager.py` — added `user_stopped` guard at the top of the restart `for`-loop (mirrors the outer-loop guard), returns early with info log.
- **New tests (100 lines):** `test_watchdog_crash_log_tail_handles_non_utf8_bytes` (non-UTF8 decode safety) + `test_watchdog_user_stopped_during_restart_backoff` (the test that caught the bug).
- **How found:** The user-stops-mid-restart test failed with 5 `start()` calls where 1 was expected — proving the restart loop ignored the user-stop. After the 14-line fix, all 61 tests pass.

---

## ensure.md Validation

### Core — Critical
- [x] **No regressions in changed packs** — every pack in the blast-radius change set returns PASS ✅
  - All 5 VS Code packs PASS (154/154 tests)
- [x] **Deadlock/concurrency integrity** — N/A (VS Code reliability fixes do not touch job queue / cascade / concurrency code)
- [x] **No sync DB calls on asyncio event loop** — N/A (VS Code fixes don't touch DB call paths)
- [x] **dev.sh includes --timeout-graceful-shutdown 10** — N/A (no dev.sh changes in this branch)

### Core — Important / Nice-to-have
- N/A (no async function conversions, no deadlock scenario changes)

### Release Gate
- **NOT run** — blast radius is scoped (moderate, single subsystem, no architecture change). Release Gate not warranted.

---

## Edge Cases Verified

| Edge case | Status |
|-----------|--------|
| Empty log_buffer on crash | ✅ PASS — clean message, no crash |
| Corrupt/non-UTF8 bytes in log_buffer | ✅ PASS — `decode(errors="replace")` → U+FFFD |
| Restart succeeds on first try | ✅ PASS — single `start()` call |
| Restart fails all attempts | ✅ PASS — status="crashed" after 5 |
| User stops while restart in progress | ✅ PASS — **bug found + fixed** (commit 81e83473) |

---

## Documentation Updated
- [x] RESULTS/2026-07-29-vscode-reliability-fixes.md — this report
- [x] PACKS.md — updated 5 VS Code pack entries with latest run
- [x] LESSONS/2026-07-29-vscode-restart-user-stopped-bug.md — bug root cause + fix

---

## Overall Status
- **Unit Tests:** ✅ PASS (61/61)
- **Integration Tests:** ✅ PASS (56/56 — proxy 41 + lifecycle 7 + security 8)
- **API Tests:** ✅ PASS (37/37)
- **ensure.md:** ✅ PASS (scoped Core requirements)
- **Quick Fixes:** 1 bug found and fixed (user_stopped guard in restart loop)
- **Testing Complete:** ✅ **READY**
