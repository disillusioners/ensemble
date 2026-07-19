# Lesson: Real-Subprocess Verification Pattern for Process-Kill Features

**Date**: 2026-07-18
**Feature**: Auto-Kill Background Processes (feature/auto-kill-bg-processes)
**Context**: Testing a feature whose core claim is "real OS processes are actually killed"

## The Pattern That Worked

When testing a process-management feature, the critical question is always: **does the test use REAL OS processes or mocks?** Mocks verify call-shape; only real processes verify actual killing.

### Verification approach (what I did)
1. Ran the test packs normally (all passed).
2. Spawned a dedicated "real-verify" opencode session to read the test files and CONFIRM which tests use real subprocesses — by quoting the exact spawn lines (`subprocess.Popen`, `asyncio.create_subprocess_exec`, `bash(command="sleep 30", ...)`).
3. Required evidence: `os.kill(pid, 0)` raising `ProcessLookupError` after cleanup (via `_pid_alive()` helper).

### Key findings
- The integration test file MIXED mocks and real processes intentionally:
  - Scenarios A/B/C (dispatch wiring) = AsyncMock (call-shape only)
  - Scenario F, Real Subprocess Smoke, Daemon Shutdown, Parallel Idempotency = REAL `sleep 30` subprocesses with `os.kill` verification
- This is acceptable: mocks verify the Tier1/Tier2 dispatch logic, real tests verify the actual kill.

### CancelledError kill verification (Test C) — critical detail
The `test_bash_cancel.py` file is explicitly a real-subprocess pack (module docstring states so). The key test `test_cancellation_at_wait_kills_subprocess`:
1. Spawns REAL `sleep 30` via bash tool
2. Verifies alive via `os.kill(pid, 0)`
3. Cancels the awaiting task
4. Verifies DEAD via `os.kill(pid, 0)`
5. Verifies registry entry removed

The CancelledError handler (`bash.py:369-395`) pattern is important to verify:
- `task.uncancel()` BEFORE kill (Python 3.11+ sticky cancellation fix)
- `asyncio.shield(_kill_process(...))` (protect kill from second cancel)
- `asyncio.shield(unregister(...))` (M2 split — kill and unregister are INDEPENDENT try/except blocks)
- `raise` at the end (always re-propagate cancellation)

### pgid TOCTOU handling (Test C related)
`_kill_process` (bash.py:138-186) resolves `target_pgid` ONCE at the top, never re-derives inside SIGTERM/SIGKILL branches. The caller captures `pgid = os.getpgid(proc.pid)` at spawn time BEFORE any awaitable cancellation window. This prevents PID-recycled group kills.

## Reusable rule
For ANY future feature that claims "processes/resources are cleaned up":
1. Don't trust green test output alone — inspect whether tests use real resources.
2. Require `os.kill(pid, 0)` / equivalent assertion as proof of death.
3. For cancellation/cleanup handlers, verify the `task.uncancel()` + `asyncio.shield()` + `raise` pattern.
4. Characterization tests for known limitations (e.g., setsid orphans surviving killpg) are GOOD — they pin documented behavior.

## What to do differently next time
Nothing — this verification flow worked well. The dedicated "real-verify" session that reads test files and quotes spawn/assertion lines is worth the extra ~30s.
