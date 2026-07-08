# Lesson: STDIO Safety Wrapper Testing

**Date:** 2026-07-08
**Branch:** `feature/stdio-safety-wrapper`
**Commit:** `8966b8c6`

## What Was Tested
The `_MCPSafeStdout` wrapper in `daemon/mcp/safe_stdout.py` that prevents `print()` calls from corrupting the JSON-RPC STDIO protocol in MCP servers.

## Key Findings

### Exception Handling Order is Critical
- `KeyboardInterrupt` and `SystemExit` are `BaseException` subclasses, NOT `Exception` subclasses
- Python checks `except` clauses in source order
- Must handle `KeyboardInterrupt` BEFORE generic `Exception` handler, otherwise it gets swallowed

### sys.executable vs python3
- When the launcher is invoked by daemon code, `sys.executable` must be used (not hardcoded `"python3"`)
- PATH might resolve `python3` to system Python lacking the daemon package (venv issue)

### detach/reconfigure Guard
- `_MCPSafeStdout.__getattr__` must raise `AttributeError` for `detach()` and `reconfigure()`
- These methods would operate on the wrong stream (stderr instead of stdout)

## Testing Gotchas
- Full non-integration suite (~1500+ tests) is too large for a single opencode session timeout
- Run targeted MCP-related tests for fast feedback: `tests/test_safe_stdout.py tests/unit/mcp/`
- The job_queue retry engine test (`test_atomic_retry_concurrent_calls_only_one_succeeds`) is a known intermittent flake — skip if it blocks with `-x`

## Test Counts
- safe_stdout: 43/43 tests
- openspace_builtin: 79/79 tests
- Full MCP sweep: 332/332 tests
