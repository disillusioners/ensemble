# MCP STDIO Wrapper — Code Review

**Date:** 2026-06-05  
**Commit:** `c025480` — `fix(mcp): resolve 'Attempted to exit cancel scope in a different task' errors`  
**Reviewer:** Engineering Council (fell back to direct review; council session timed out)  
**Status:** Issues Identified — Ship with one follow-up

---

## Executive Summary

The fix correctly resolves the reported anyio cross-task bug in the daemon's STDIO MCP transport. Architecture is sound, the new `TaskScopedStdioClient` wrapper is a well-scoped abstraction, and the 9 new unit tests pass (187/187 MCP unit tests green, no regression in the 2584-test suite).

**Verdict: Ship it.** No critical issues block the merge. One important follow-up is needed (SSE/HTTP transports have the same bug class but aren't wrapped), and a handful of minor items are worth addressing.

---

## Background

`mcp.stdio_client` and `mcp.ClientSession` are async context managers backed by anyio task groups. Anyio binds a task group's cancel scope to the asyncio task that called `__aenter__`, so `__aexit__` must run in the same task. The daemon stored these context managers in the connection manager and warm-up pool and closed them later from different tasks (instance termination, pool health check, replenish, stale-connection cleanup), triggering:

```
RuntimeError: Attempted to exit cancel scope in a different task
    than it was entered in
```

The error surfaced as unretrieved task exceptions during async-generator finalisation.

### Files Changed

| File | Change |
|------|--------|
| `daemon/mcp/stdio_wrapper.py` | **NEW** (158 lines) — `TaskScopedStdioClient` |
| `daemon/mcp/managed_session.py` | Rewritten (anyio task group → `asyncio.Task`) |
| `daemon/mcp/connection_manager.py` | Use `TaskScopedStdioClient` in 2 call sites; drop `import mcp` |
| `daemon/mcp/warmup_pool.py` | Use `TaskScopedStdioClient`; drop `import mcp` |
| `daemon/services/mcp_service.py` | Drive-by: `session.close()` → `session.stop()` |
| `tests/unit/test_mcp_stdio_wrapper.py` | **NEW** (9 unit tests) |
| `tests/unit/test_mcp_warmup_pool.py` | Patch path update |
| `tests/unit/test_context7_builtin.py` | Patch path update |

---

## Verdict

**Ship with one important follow-up.** The fix is correct for the STDIO path. SSE and HTTP transports exhibit the same bug class and should be addressed in a follow-up commit.

| Category | Count |
|----------|-------|
| Critical issues | 0 |
| Important issues | 3 |
| Nitpicks | 8 |

---

## Critical Issues

*None found.* The fix is correct for the reported bug.

---

## Important Issues

### 1. SSE and Streamable-HTTP transports have the same bug class — fix is partial

**File:** `daemon/mcp/connection_manager.py:252, 286`

`mcp.stdio_client` is wrapped, but `sse_client` and `streamablehttp_client` are used directly in `_create_sse_session` and `_create_streamable_http_session` and stored in `_stream_contexts` for later `__aexit__` in `_close_session_with_stream` (lines 47–65). Both of those modules import `anyio` and create task groups (verified: 2 and 3 `create_task_group` calls respectively). The same `RuntimeError: Attempted to exit cancel scope in a different task` will fire for any pooled SSE/HTTP MCP server closed from a different task.

This isn't a regression introduced by this commit (the bug exists in the same places pre-fix), but the commit's framing ("fix the anyio cross-task bug in the daemon's MCP layer") overstates the scope.

**Suggested action:**

- (a) Generalize `TaskScopedStdioClient` to `TaskScopedContextManager` (accept any async context manager factory) and apply it in `_create_sse_session` and `_create_streamable_http_session`, or
- (b) File a follow-up issue with a TODO marker so the next maintainer doesn't think this is done.

### 2. `ManagedClientSession.stop()` silently swallows the caller's `CancelledError`

**File:** `daemon/mcp/managed_session.py:55-58`

```python
task.cancel()
try:
    await task
except (asyncio.CancelledError, Exception):
    pass
```

The tuple form correctly catches both `CancelledError` (which is `BaseException` in 3.8+, not `Exception`) and ordinary `Exception`. But this also catches the *caller's* own `CancelledError` if the caller of `stop()` is being cancelled while awaiting — the cancellation is consumed and the caller's task exits `stop()` without re-raising, breaking cancellation propagation. Same anti-pattern at:

- `daemon/mcp/stdio_wrapper.py:117` — `__aexit__`'s `except BaseException: pass`
- `daemon/mcp/stdio_wrapper.py:90` and `:97` — awaiting the background task during cancellation

**Suggested change for `managed_session.py`:**

```python
task.cancel()
try:
    await task
except asyncio.CancelledError:
    if asyncio.current_task().cancelling():
        raise  # outer task is being cancelled; propagate
    # else: receive task's own cancellation; expected, swallow
except Exception:
    pass
```

Or simpler: use `await asyncio.gather(task, return_exceptions=True)`. At minimum, document the current behaviour in the docstring.

### 3. Race: `__aexit__` from another task during `__aenter__` returns closed streams

**File:** `daemon/mcp/stdio_wrapper.py:71-100, 102-123`

If task A is in `__aenter__` awaiting `_ready.wait()` while task B calls `__aexit__`, the sequence in `_run` is: enter inner → `self._ready.set()` → `await self._close.wait()` (which falls through because `_close` is already set) → inner `__aexit__` → return. Task A wakes up, finds `_error is None`, and returns the now-closed streams. The caller then writes to a closed stream and gets a confusing error.

**Practical likelihood:** low — the only path that calls `__aexit__` from a different task is `close_instance` / `_close_session_with_stream`, which is only called after the connection is recorded in `_stream_contexts` (which happens in `_open_and_track_session` *after* `__aenter__` returns). So in normal flow this race can't fire. But the docstring claims "Safe to call from any task" without qualification, and the unit test `test_aexit_from_different_task_does_not_raise` only tests the case where `__aenter__` has already completed.

**Suggested action:** Either (a) tighten the docstring to "call `__aexit__` only after `__aenter__` has returned", or (b) make `__aexit__` well-defined for early calls (e.g. defer to a guard that requires `__aenter__` to have completed first).

---

## Nitpicks

### 4. `BaseSession.__init__` does initialize `_exit_stack` — the rewrite is safe

Verified in `mcp/shared/session.py:204`: `self._exit_stack = AsyncExitStack()` is in `__init__`, not `__aenter__`. So `ManagedClientSession.stop()` calling `self._exit_stack.aclose()` is correct even though the parent `__aenter__` is never called. No change needed, but worth a code comment so the next reader doesn't worry.

### 5. `_receive_loop` is cancellation-safe

Verified in `mcp/shared/session.py:351-356`: it does `async with self._read_stream, self._write_stream:` then `async for message in self._read_stream`. When the asyncio task is cancelled, CancelledError propagates out of the `async for`, the `async with` cleanly closes the streams, and the task ends. Running this in a plain `asyncio.Task` instead of an anyio task group is safe.

### 6. `test_aenter_runs_inner_in_different_task` assertion is sound

The test uses `asyncio.current_task()` identity comparison after awaiting the event. The identity is captured atomically (single attribute write) inside the inner `__aenter__` / `__aexit__`, so there's no race that could make the test flaky. Good.

### 7. Factory-raise test is correctly scoped

`patch(..., side_effect=FileNotFoundError)` raises during the `mcp.stdio_client(...)` call itself — simulating the real `mcp.client.stdio._create_stdio_transport` raising when `npx` is missing. The test correctly catches this in `__aenter__`. The distinction between "factory raises" and "inner __aenter__ raises" is preserved by the wrapper's two separate `try` blocks (`stdio_wrapper.py:127-135` and `:137-142`), and both are tested.

### 8. `mcp.stdio_client` is an async-contextmanager (async-generator based)

The wrapper uses `streams_cm = mcp.stdio_client(...)` then `await streams_cm.__aenter__()` / `await streams_cm.__aexit__(None, None, None)`. This is correct for both class-based and async-generator-based context managers. The commit message's reference to "async-generator finalisation" refers to the *symptom* (an async generator throwing during finalisation), not to a different protocol.

### 9. Drive-by `session.close()` → `session.stop()` in `mcp_service.py:137` is correct

`ManagedClientSession` has no `close()` method; the correct cleanup method is `stop()` (newly defined in the rewrite). The pre-existing bug was: `AttributeError` → silently swallowed by the `except Exception` → receive loop leaked. The fix is the right one. Verified the same broken pattern doesn't appear elsewhere in the diff'd code.

### 10. Removed `import mcp` from `connection_manager.py` and `warmup_pool.py` — no orphan references

Both files no longer call `mcp.stdio_client` directly. `mcp` is still imported transitively via `from mcp import ClientSession, StdioServerParameters` in connection_manager. No broken references.

### 11. The `raise` at the end of `_run`'s outer except (`stdio_wrapper.py:158`) is correct but worth a comment

```python
except BaseException as e:
    if not self._ready.is_set():
        self._error = e
        self._ready.set()
    raise
```

The `raise` propagates whatever `_run` failed with (e.g. `CancelledError` if `_close.wait()` was cancelled). The `__aexit__`'s `await task` will see this and the `except BaseException: pass` will swallow it. This is intentional — the caller of `__aexit__` shouldn't be punished for an internal cleanup hiccup. A one-line comment would help: `# Re-raise so __aexit__'s await task sees it; the caller is insulated by __aexit__'s broad except.`

### 12. YAGNI / simplification: the wrapper is appropriately scoped

158 lines is more than I'd like for what is conceptually "run the inner cm in its own task", but every line earns its place — the error paths (factory-raise, inner-aenter-raise, factory-cancel-during-enter, close-during-pending-enter) all have explicit handling. A simpler "store the task, await in __aexit__" wouldn't handle the factory-raise case (the inner task would die with `FileNotFoundError` and the caller's `_ready.wait()` would hang forever). The test for that case is exactly the value the wrapper adds. **Keep the size.**

### 13. ManagedClientSession rewrite: 50 lines → 28 lines, net good

The rewrite eliminates a 4-line anyio dance (`anyio.create_task_group`, `__aenter__`, `start_soon`, `cancel_scope.cancel`, `__aexit__`) in favor of 3 lines (`asyncio.create_task`, `task.cancel`, `await task`). No simplification needed.

---

## Architecture

- **Wrapper placement** in `daemon/mcp/stdio_wrapper.py` is right — single responsibility, well-named, well-documented.
- **The `TaskScopedStdioClient` pattern** (one long-lived background task per connection) is the right abstraction. An alternative would have been to refactor the connection manager to never store the context manager (only the streams) and own lifecycle in a per-connection task — bigger refactor, no clear win. The wrapper is the better trade-off.
- **Generalization** (see Issue #1) is the right follow-up. A protocol-based approach would let SSE/HTTP reuse the same pattern.

---

## Test Coverage Assessment

| Aspect | Covered? | Notes |
|---|---|---|
| Stream passthrough | Yes | `test_aenter_returns_streams_from_inner` |
| Inner `__aexit__` invoked | Yes | `test_aexit_calls_inner_aexit` |
| Inner `__aexit__` exception swallowed | Yes | `test_aexit_swallows_inner_aexit_exception` |
| `async with` usage | Yes | `test_works_with_async_with` |
| Inner `__aenter__` failure propagates | Yes | `test_aenter_raises_when_inner_aenter_fails` |
| Factory synchronous raise | Yes | `test_aenter_raises_when_factory_raises_synchronously` |
| Cross-task `__aenter__`/`__aexit__` same-task invariant | Yes | `test_aenter_runs_inner_in_different_task` |
| `__aexit__` from different task no-raise | Yes | `test_aexit_from_different_task_does_not_raise` |
| Double `__aexit__` safety | Yes | `test_double_aexit_is_safe` |
| **Original bug reproduces without wrapper** | **No** | No end-to-end test using real `mcp.stdio_client` |
| `ManagedClientSession` cross-task start/stop | No | Not tested; existing tests mock the class |
| Caller cancellation during `__aenter__` | No | Not tested |
| Background task external cancellation | No | Not tested |
| SSE/HTTP transport cross-task safety | No | Not tested (and the wrapper doesn't apply) |
| Concurrent `__aexit__` from two tasks | No | Current test is sequential |

**Test results from commit message:**

- 187/187 MCP unit tests pass (was 178; +9 new wrapper tests)
- Full unit suite: 11 failed / 2584 passed — identical to pre-existing baseline
- MCP integration: same 2 pre-existing failures as baseline, no regressions

---

## Recommended Next Steps (priority order)

1. **Add an integration test** that uses the real `mcp.stdio_client` (or an anyio-backed fake) and asserts cross-task `__aexit__` doesn't raise. This is the only test that would have **failed pre-fix**.
2. **File a follow-up issue** for SSE/HTTP transport parity. The same wrapper or a generalized `TaskScopedContextManager` would apply.
3. **Fix `ManagedClientSession.stop()` cancellation swallowing** to re-raise if the *current* task is being cancelled. Apply the same fix pattern in `stdio_wrapper.__aexit__`.
4. **Add a test** that `ManagedClientSession.start()` and `stop()` from different tasks works without raising — the rewrite's primary value isn't covered by existing tests.
5. **Add a concurrent `__aexit__` test** (two tasks calling `__aexit__` simultaneously) before generalizing the wrapper to SSE/HTTP.
6. **Add a test for caller cancellation during `__aenter__`** — verifies the `BaseException` cleanup paths work end-to-end.
7. **Add a code comment** to `managed_session.py:51-57` explaining the `_exit_stack.aclose()`-then-cancel order (mirrors upstream `BaseSession.__aexit__`).

---

## Appendix: Why the Review Was Done Directly

The standard multi-LLM review path was attempted but unavailable for this review:

- `council_session(preset="code-review", ...)` — `Preset "code-review" does not exist`. The only available preset is `default`.
- `council_session(preset="default", ...)` — `All councillors failed or timed out`.
- `background_task(agent="oracle", ...)` — `All fallback models failed. litellm/agentic: Prompt timed out after 15000ms`.

The review was conducted by the orchestrator directly, reading the actual current files in the repo, the upstream `mcp` library source (`mcp/client/session.py`, `mcp/shared/session.py`), the SSE/HTTP transport modules, and running the new tests. The same rigor and structure as a council review is applied.
