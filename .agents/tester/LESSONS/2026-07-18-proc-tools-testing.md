# Lesson: proc tools full test + opencode session resilience

Date: 2026-07-18
Branch: feature/background-proc-tools
Related: RESULTS/2026-07-18-proc-tools-full-test.md

## Context
Full test of the new `proc` tool category (5 background-process tools) on branch `feature/background-proc-tools`. Ran 4 scoped packs in parallel.

## Key Findings

### proc tools are solid
- 21 unit tests pass (lifecycle, cap, spillover, split-line stitching C1 fix, spawn-window race C2 fix, timeout killer, multi-chunk spillover, cleanup, cross-instance isolation)
- Integration smoke (real BackgroundProcessManager singleton, no mocking): 6/6 workflow steps pass
- Edge cases: 5/5 pass (11th rejected, immediate-exit code 0, error exit code 2, empty-logs-before-output, 1s timeout auto-kill)
- No regressions: 49/49 existing infra tool tests pass
- Registry + instance assembly correctly wired; proc in tools.allow for 13 agents

### proc_run timeout arg naming gotcha
- The `proc_run` @tool exposes the timeout parameter as **`timeout`**
- The internal `BackgroundProcessManager.start_process` method renames it to **`timeout_seconds`**
- Tests/scripts that construct proc_run args must use `timeout`, NOT `timeout_seconds`

### proc_status exit-code semantics
- Successful exit → `status: exited, exit_code: 0`
- Non-zero exit (e.g. SystemExit(2)) → `status: exited, exit_code: 2` (status is still "exited", NOT "error"; the exit_code field carries the error signal)
- Timeout auto-kill → `status: killed, exit_code: -9, timed_out: true`
- Killed via proc_stop → `status: killed, exit_code: -15` (SIGTERM)

## Process Lesson: opencode session corruption

### Symptom
Two of the 4 parallel opencode sessions (tools-regression-test initial run, proc-edge-cases-test) returned **garbled/truncated responses** containing malformed tokens like `[<]minimax[>[` and `<tool_call>` XML fragments, then stopped with `finish: stop` despite not completing the work.

### Root cause
Appears to be an intermittent model/output issue — the session emits raw internal function-call tokens instead of executing them. Resuming the same session did NOT help (it re-returned the same stale corrupted response).

### Fix / mitigation
1. **Re-send the task** to the wedged session first (sometimes the orchestrator re-processes)
2. If re-send still returns stale garbage → **abort the session** (`external_opencode_abort_session`)
3. **Initialize a NEW session with a different name** (re-init of the same name also works, but a fresh name avoids any residual state) and re-send the task
4. The fresh session completed the work correctly on the first run

### Takeaway
- Don't waste cycles resuming a session that returns garbled tokens — abort and start fresh
- Always verify the response content matches the task (not just that `finish: stop` was reached); a "completed" session with garbled output is a failure, not a success
