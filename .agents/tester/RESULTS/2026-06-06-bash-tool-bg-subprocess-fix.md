# Test Report: Bash Tool Backgrounded Subprocess Fix

**Date:** 2026-06-06
**Branch:** `feature/fix-bash-tool-hang-backgrounded-subprocess`
**Commits Under Test:** `9e678b3`, `0ad4494`, `a77f2ba`
**Sessions:** `ens/bash-fix-tests`, `ens/ensure-md`

---

## Summary

| Check | Result | Details |
|-------|--------|---------|
| Pack 1: Targeted tests (`tests/test_tools.py`) | ✅ PASS | 35/35 passed (15 bash + 20 filesystem), 5s |
| Pack 2: Core unit pack (`core_unit_test.sh`) | ✅ PASS | 668/668 passed (19 files), 17s |
| ensure.md (dev.sh 30s stability) | ✅ PASS | Ran stably 30s, exit 124 (timeout kill) |
| **Overall Status** | **✅ READY** | All checks pass |

**Quick Fixes Applied:** 1 (unrelated pre-existing config drift)

---

## Pack 1: Targeted Bash Tool Tests

**Status:** ✅ PASS
**Total:** 35 | **Passed:** 35 | **Failed:** 0 | **Errors:** 0
**Duration:** 5s

### Bash Tool (15 tests — all pass)

Standard tests (12):
- `test_bash_simple_command` ✅
- `test_bash_command_with_output` ✅
- `test_bash_nonzero_exit_code` ✅
- `test_bash_stderr_captured` ✅
- `test_bash_timeout` ✅
- `test_bash_working_directory` ✅
- `test_bash_with_environment_variable` ✅
- `test_bash_negative_timeout_returns_error` ✅
- `test_bash_timeout_exceeds_max_returns_error` ✅
- `test_bash_zero_timeout_means_no_timeout` ✅
- `test_bash_float_timeout_works` ✅
- `test_bash_float_timeout_with_short_duration` ✅

**New tests verifying the fix (3 — all pass) ★:**
- `test_bash_backgrounded_subprocess_returns_immediately` ✅ — bare `&` no longer hangs
- `test_bash_nohup_background_returns_immediately` ✅ — `nohup ... &` no longer hangs
- `test_bash_process_group_killed_on_timeout` ✅ — `os.killpg` cleanup works on timeout

### Filesystem Tools (20 tests — all pass)
- `TestListDirectoryTool` (7 tests) ✅
- `TestReadFileTool` (7 tests) ✅
- `TestGlobFilesTool` (6 tests) ✅

---

## Pack 2: Core Unit Test Pack

**Status:** ✅ PASS
**Total:** 668 | **Passed:** 668 | **Failed:** 0 | **Errors:** 0
**Duration:** 17s (well under 120s timeout)
**Scope:** 19 test files including `test_tools.py`

No regressions detected. The bash tool fix (temp files + process group isolation) does not impact any other core module.

---

## ensure.md Validation

**Status:** ✅ PASS
**Requirement:** `dev.sh` runs stably for 30 seconds
**Result:** Exit code 124 (killed by `timeout` after 30s = stable run)
**Evidence:**
- Uvicorn started on `http://0.0.0.0:8079`
- "Application startup complete."
- MCP warmup complete: 2 servers ready (webfetch, context7)
- WorkerPool started: 4 workers
- JobProcessor started
- 0 exceptions/tracebacks in 126 log lines
- Clean shutdown on SIGTERM
- **Did NOT touch port 8088** (ensemble system port)

**Note:** Pre-existing dev-mode warning "No SOURCE_CREDENTIAL_KEY provided" is expected, not a failure.

---

## Quick Fixes Applied

| Fix | File | Root Cause | Commit |
|-----|------|-----------|--------|
| `MAX_INSTANCE_HISTORY` 500 → 300 | `daemon/constants.py:67` | Config/code drift between `constants.py` (500), `config.yaml` (300), and test expectation (300). Pre-existing, unrelated to bash tool fix. | `0754613` — "test: fix max_instance_history default mismatch (500→300)" |

---

## Bug Fix Verification

The original bug: `proc.communicate()` hung indefinitely when shell commands backgrounded subprocesses (e.g., `nohup sleep 10 &`), because backgrounded children inherited pipe FDs and `communicate()` waited for EOF that never came.

**Verified fixed via:**
1. **`test_bash_backgrounded_subprocess_returns_immediately`** — `(sleep 10 & echo 'done')` returns immediately with `done` output, no hang
2. **`test_bash_nohup_background_returns_immediately`** — bare `nohup sleep 10 & echo 'started'` returns immediately (this is the critical regression test — without explicit redirects, the pipe FD inheritance caused the hang)
3. **`test_bash_process_group_killed_on_timeout`** — `sleep 60 & ... wait` with 2s timeout: returns timeout error AND the backgrounded sleep PID is confirmed dead via `os.kill(pid, 0)` existence check

**Fix mechanism confirmed:**
- Temp file-based capture (`tempfile.mkstemp`) replaces pipes — backgrounded children holding FDs is harmless
- Process group isolation (`start_new_session=True`) enables group kill on timeout
- `os.killpg(os.getpgid(proc.pid), signal.SIGTERM/SIGKILL)` kills entire process tree
- `_read_file_bytes` helper safely reads temp files (handles None/missing paths)
- Temp files cleaned up in all paths (success, timeout, error)

---

## Overall Status

- **Unit Tests:** ✅ PASS (35/35 targeted, 668/668 broad)
- **ensure.md:** ✅ PASS (dev.sh stable 30s)
- **Quick Fixes:** 1 applied (unrelated pre-existing config drift)
- **Regressions:** 0
- **Testing Complete:** ✅ **READY**
