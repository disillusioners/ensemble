# Test Report: STDOUT Safety Wrapper for STDIO MCP Servers

**Date:** 2026-07-08
**Branch:** `feature/stdio-safety-wrapper`
**Session:** `stdio-safety-test` (ses_0bd080a14ffeEu408BYu7KNwrM)
**Commit:** `8966b8c6` (working tree clean)

---

## Summary

- **Overall Verdict:** ✅ **PASS** for safe_stdout/MCP scope
- **Total targeted tests:** 332/332 passed (0 failures)
- **Quick Fixes Applied:** W1/S1/S2 committed as `8966b8c6`
- **Pre-existing failures:** 5 unrelated tests (job queue proxy, retry engine) — not introduced by this work

---

## Test Counts

| Suite | Total | Passed | Failed | Time |
|---|---|---|---|---|
| `tests/test_safe_stdout.py` | **43** | **43** | 0 | 3.78s |
| `tests/unit/mcp/test_openspace_builtin.py` | **79** | **79** | 0 | 0.69s |
| `tests/unit/mcp/` (broader MCP) | **79** | **79** | 0 | 0.80s |
| MCP-related sweep (safe_stdout + builtin + context7 + rag) | **332** | **332** | 0 | 5.09s |

---

## Verification Results

### 1. Text redirect — ✅ PASS
- `print()` → stderr: confirmed by `test_print_routed_through_wrapper_lands_on_stderr`
- `sys.stdout.write("text")` → stderr: confirmed by `test_write_goes_to_stderr_not_stdout`
- `sys.stdout.buffer.write(bytes)` → real stdout: confirmed by `test_writes_through_buffer_reach_real_stdout_only`

### 2. Subprocess integration — ✅ PASS
- `test_safe_stdout_launcher_keeps_stdout_clean` spawns `python -m daemon.mcp.safe_stdout <module>` with dummy calling `print("corrupting output")`
- Asserts stdout clean, stderr contains text, binary protocol bytes flow through stdout

### 3. Exception handling — ✅ PASS
- KeyboardInterrupt re-raised: `test_keyboard_interrupt_is_reraised`
- SystemExit(int): `test_system_exit_code_is_propagated`
- SystemExit("string"): `test_system_exit_with_string_code_does_not_crash`
- SystemExit(None): `test_system_exit_with_no_code_returns_zero`

### 4. Backward compatibility — ✅ PASS
- webfetch.py: `command: "uvx"` — NOT wrapped ✓
- context7.py: `command: "npx"` — NOT wrapped ✓
- openspace.py STDIO: uses `daemon.mcp.safe_stdout` wrapper ✓
- openspace.py HTTP mode: NO wrapper, unaffected ✓

### 5. detach/reconfigure guard — ✅ PASS
- `__getattr__` raises `AttributeError` for `detach()` and `reconfigure()`
- Tests: `test_detach_raises_attribute_error`, `test_reconfigure_raises_attribute_error`

---

## Quick Fixes Applied

**Commit `8966b8c6`** — "fix: guard detach/reconfigure in safe_stdout + docstring consistency"
- W1: `detach()` and `reconfigure()` now raise AttributeError
- S1: Module docstring `python3 -m` → `python -m`
- S2: Tests for detach/reconfigure guard

Files: `daemon/mcp/safe_stdout.py` (+13/-1), `tests/test_safe_stdout.py` (+26/-1)

---

## Pre-existing Failures (NOT related to safe_stdout)

| Test | File | Reason |
|---|---|---|
| `test_atomic_retry_concurrent_calls_only_one_succeeds` | `tests/job_queue/test_job_retry_engine.py` | Intermittent SQLite threading race |
| `test_completed_job_mirror_overridden_by_active_instance` | `tests/unit/services/test_job_queue_proxy_phase1.py` | Job queue proxy status derivation |
| `test_active_job_ids_subquery_includes_both_states` | `tests/unit/services/test_jq_proxy_phase3_query_migration.py` | SQL string ordering |
| `test_active_job_ids_subquery_protects_queued` | same file | Same SQL ordering flake |
| `test_c3_subquery_protects_queued_locks` | `tests/unit/services/test_jq_proxy_phase3_regression.py` | Same SQL ordering flake |
