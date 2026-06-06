# Review: Bash Tool Backgrounded Subprocess Fix (commit 9e678b3)

**Date:** 2026-06-06
**Status:** ✅ Pass with caveats — 1 critical issue (fast-follow), several improvements recommended
**Scope:** `daemon/tools/bash.py` + `tests/test_tools.py` (3 new tests)
**Bug report:** `docs/bugs/bash-tool-hangs-on-backgrounded-subprocess.md`

---

## Verdict

**The core fix is sound.** The file-based output capture correctly eliminates the pipe-EOF hang, and the process-group isolation is correctly implemented. The 3 new tests pass and would have caught the original bug.

**However, there are issues to address:**

| Severity | Count | Action |
|---|---|---|
| 🔴 Critical | 2 | Fix before merge or fast-follow |
| 🟡 Warning | 5 | Should fix |
| 🟢 Suggestion | 5 | Nice to have |

---

## 🔴 Critical

### 1. Temp file leak + `UnboundLocalError` masks real errors in `finally` block
**File:** `daemon/tools/bash.py:210-220`
**Found by:** review-impl

**The problem:** Variables `stdout_path`, `stderr_path`, `stdin_path` are assigned inside the `try` block (lines 97-114), but the `finally` cleanup block (line 214) iterates over them unconditionally. If **any** exception occurs after `stdout_path` is assigned but before `stderr_path` or `stdin_path` are assigned, the `finally` block raises `UnboundLocalError`, which:
1. **Masks the real exception** (e.g., `OSError: disk full`, `PermissionError`)
2. **Leaks the temp files** that were already created (the `for` loop aborts before reaching `os.unlink`)
3. **Propagates as unhandled exception** — langchain's `BaseTool.arun` re-raises it, potentially crashing the agent node

**Trigger scenarios:**
- `tempfile.mkstemp` for stderr fails (disk full, too many open files)
- `open(stdout_path, "w+b")` succeeds but `open(stderr_path, "w+b")` raises
- Exception between line 97 and line 109

**Fix:**
```python
# BEFORE the try block:
stdout_path = None
stderr_path = None
stdin_path = None
stdout_file = None
stderr_file = None
stdin_file = None
try:
    stdout_fd, stdout_path = tempfile.mkstemp(...)
    ...
```

---

### 2. Test 2 (`test_bash_nohup_background_returns_immediately`) does NOT reproduce the original bug
**File:** `tests/test_tools.py:137-146`
**Found by:** review-tests

**The problem:** The test command `nohup sleep 10 > /dev/null 2>&1 & echo 'started'` includes explicit `> /dev/null 2>&1` redirections. These redirects mean the backgrounded process **never inherits the pipe FDs** — the very condition that caused the original hang. The opencode session **empirically verified** this test passes against the OLD pipe-based code (returns in 0.006s without hanging). The test provides zero regression value.

**Fix:** Drop the explicit redirects:
```python
"command": "nohup sleep 10 & echo 'started'"
```
This command reliably hangs under the old PIPE-based code (verified).

---

## 🟡 Warnings

### 3. Test 3 has NO assertion on process group kill verification
**File:** `tests/test_tools.py:163-172`
**Found by:** review-tests

The test runs `pgrep -f "sleep 60"` but never **asserts** the result. It just cleans up silently. The test passes even if the process group kill completely failed. The pgrep result is dead code.

**Fix:** Add an assertion (with a small grace window for SIGKILL delivery):
```python
assert check.returncode != 0, f"sleep 60 still alive: {check.stdout}"
```

### 4. `_kill_process` exception handling too narrow
**File:** `daemon/tools/bash.py:38-50`
**Found by:** review-impl

Only `(ProcessLookupError, PermissionError)` is caught. Other `OSError` variants (EINTR, EIO, EINVAL) would propagate and crash the timeout handler. Also, there's a PID-reuse race: process could exit between `getpgid()` and `killpg()`, and the PID could be reused for a different process group.

**Fix:** Broaden to `except OSError:` and capture the PGID before any `await`:
```python
pgid = os.getpgid(proc.pid)  # capture before any await
try:
    os.killpg(pgid, signal.SIGTERM)
except OSError:
    pass
```

### 5. `pkill -f "sleep 60"` in test cleanup is unsafe
**File:** `tests/test_tools.py:172`
**Found by:** review-tests

`pkill -f` matches any process whose command line contains the substring. On shared CI or dev machines, this could kill unrelated processes. Combined with Finding #3 (no assertion), the cleanup is the only thing the test actually does — and it's indiscriminate.

**Fix:** Use PID-scoped cleanup or unique markers instead of pattern matching.

### 6. `os.write(stdin_fd, ...)` doesn't handle partial writes
**File:** `daemon/tools/bash.py:112`
**Found by:** review-impl

POSIX allows `write()` to return fewer bytes than requested. For very large inputs, the child could see truncated stdin. (In practice, file writes on macOS/Linux are usually atomic, so this is theoretical.)

**Fix:** Use a file object instead:
```python
with open(stdin_path, 'wb') as f:
    f.write(input.encode())
```

### 7. EXIT CODE dropped when output is truncated (pre-existing)
**File:** `daemon/tools/bash.py:199-207`
**Found by:** review-impl

When output exceeds 150k chars, `content[:150000]` chops off the `EXIT CODE: N` suffix (appended last). Agent can't tell if the command succeeded. Pre-existing — not a regression from this fix.

**Fix:** Preserve the exit code line in the truncated output.

---

## 🟢 Suggestions

### 8. Tests 1 & 2 leak orphan `sleep 10` processes
**File:** `tests/test_tools.py:124-146`
**Found by:** review-tests

On success, the backgrounded `sleep 10` is orphaned and runs for ~10s after the test passes. Use shorter sleep durations (`sleep 1`) to reduce process table pollution.

### 9. No test for `input` (stdin) parameter
**File:** `daemon/tools/bash.py:108-114`
**Found by:** review-tests

The new temp-file-based stdin path is completely untested.

**Suggested test:**
```python
async def test_bash_input_via_stdin(self):
    result = await bash.ainvoke({"command": "cat", "input": "hello from stdin"})
    assert "hello from stdin" in result
```

### 10. No test for large output (>64KB pipe buffer limit)
**Found by:** review-tests

The fix's primary motivation was eliminating the pipe buffer limit, but no test verifies large output capture.

### 11. No test for `command: list[str]` (exec) path
**Found by:** review-tests

`bash.py:118-126` (`create_subprocess_exec`) is never exercised.

### 12. Comment block is stale
**File:** `daemon/tools/bash.py:92-93`
**Found by:** review-impl

Comment says "NamedTemporaryFile with delete=False" but code uses `tempfile.mkstemp`. Update for accuracy.

---

## What the Fix Gets Right ✅

- **File-based capture** correctly eliminates the pipe-EOF hang
- **`proc.wait()`** instead of `proc.communicate()` is correct (no pipes to read)
- **`start_new_session=True`** correctly isolates child into own PGID — `os.killpg` cannot kill the agent
- **`actual_timeout = None` for `timeout=0`** correctly disables timeout
- **Output format** `STDOUT: ... / STDERR: ... / EXIT CODE: N` preserved exactly
- **`errors="replace"`** on decode improves over old code (was strict `decode()`, would crash on binary)
- **Timeout path** reads best-effort output before reporting error
- **Windows fallback** is structurally correct (though see Warning #4 for `os.killpg` guarding)

---

## Recommended Priority

| # | Fix | Effort | Impact |
|---|-----|--------|--------|
| 1 | Initialize path vars to `None` before `try` | 5 min | Prevents exception masking + file leaks |
| 2 | Drop `> /dev/null 2>&1` from Test 2 | 1 min | Restores regression value |
| 3 | Add assertion to Test 3 | 5 min | Actually verifies process group kill |
| 4 | Broaden `_kill_process` to `except OSError` | 5 min | Prevents crash on edge-case signals |
| 5 | Replace `pkill -f` with PID-scoped cleanup | 10 min | Prevents CI accidents |
| 6-7 | Address partial writes, truncation | 15 min | Edge-case correctness |
| 8-12 | Coverage gaps, suggestions | 30 min | Long-term test health |

**Items 1-5 should be addressed before merge or as a fast-follow commit.**

---

## Sessions Used

| Session | Target | Status |
|---------|--------|--------|
| `review-impl` | `daemon/tools/bash.py` | ✅ Complete |
| `review-tests` | `tests/test_tools.py` | ✅ Complete |
