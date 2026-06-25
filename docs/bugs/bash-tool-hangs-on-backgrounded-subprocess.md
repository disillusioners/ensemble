# Bug: Bash Tool Hangs on Compound Commands with Backgrounded Subprocess

**Date:** 2026-06-04
**Status:** Investigated (pending fix)
**Severity:** High (agent becomes unresponsive, no LLM call for 20+ minutes)

---

## Summary

The `bash` tool in `daemon/tools/bash.py` hangs indefinitely when the AI agent invokes a compound command that includes a backgrounded long-running process via `nohup ... &`. The LangGraph agent node stops progressing — the tool call is logged but no subsequent LLM invocation occurs, effectively freezing the agent.

**Example command (the actual user invocation that triggered the bug):**
```bash
nohup ./dev.sh > /tmp/ensemble-dev-migration.log 2>&1 &
echo "PID: $!"
sleep 5
cat /tmp/ensemble-dev-migration.log
```

After the LLM emits this `bash` tool call, no further LLM invocations happen for 20+ minutes, even though the main event loop continues to run (other background services like `job_processor` keep tracing every 30 seconds).

---

## Root Cause Analysis

The bash tool is **correctly async** and uses non-blocking subprocess I/O, so the tool itself is not the direct cause. The real issue is in how the underlying `asyncio.create_subprocess_shell` interacts with shell control flow that includes a backgrounded job that does not fully detach from the parent's stdio/pipes.

### Primary suspect: backgrounded child holding parent shell open

When a shell command like:
```bash
nohup ./dev.sh > /tmp/ensemble-dev-migration.log 2>&1 &
echo "PID: $!"
sleep 5
cat /tmp/ensemble-dev-migration.log
```
is executed via `asyncio.create_subprocess_shell`, the parent shell (`sh -c "..."`) does not exit until **all** of its file descriptors are closed, including the stdout/stderr pipes inherited by the backgrounded `nohup ./dev.sh` process.

Even though we redirect `nohup`'s stdout/stderr to a file (`> /tmp/ensemble-dev-migration.log 2>&1`), the dev server (uvicorn) may hold open **additional inherited file descriptors** from the shell — for example, the original pipe ends used to capture `STDOUT`/`STDERR` in the bash tool:

```python
# daemon/tools/bash.py:63-68
proc = await asyncio.create_subprocess_shell(
    command,
    stdout=asyncio.subprocess.PIPE,   # pipe held by asyncio
    stderr=asyncio.subprocess.PIPE,   # pipe held by asyncio
    stdin=asyncio.subprocess.PIPE if input else asyncio.subprocess.DEVNULL,
    cwd=workdir,
)
```

When the shell backgrounds a process with `&`, that child **inherits** the shell's own stdin/stdout/stderr file descriptors by default. The `nohup` only protects against SIGHUP; it does **not** close inherited FDs. The backgrounded `nohup ./dev.sh` therefore keeps the shell's stdout/stderr write-ends alive.

The parent shell will wait for these FDs to close. `cat /tmp/ensemble-dev-migration.log` and `echo "PID: $!"` both complete, but the shell's `wait` semantics (depending on `sh` flavor) can keep the entire script from exiting as long as any backgrounded child still has open stdout/stderr pipes shared with the parent.

### Secondary suspects

| # | Cause | Likelihood |
|---|-------|-----------|
| 1 | Backgrounded subprocess inherits shell's stdout/stderr pipe FDs, parent shell waits on them | **High** |
| 2 | uvicorn with `--reload` starts a `watchdog` observer that holds non-stdio FDs and waits for parent shell exit (less likely without the FD inheritance angle) | Medium |
| 3 | Subprocess holds a write-end of a pipe because the agent's working directory is on a watched path, causing uvicorn to keep restarting and never fully close | Low |
| 4 | LangGraph's `ToolNode` not properly handling async `coroutine=` tools (would be a separate, more serious bug — but tests for `bash.ainvoke` pass, so this is unlikely) | Low |

The most likely cause is **(1)**: the backgrounded process inherits the shell's pipe FDs and never closes them, so the parent `sh -c` command never returns, `asyncio.wait_for(proc.communicate(), ...)` never completes, and the tool result is never added to the LangGraph state — the agent freezes.

---

## Evidence From the Logs

```
11:28:29 - daemon.graph - INFO - [LLM] Invoking LLM (STANDARD) with 38 messages (...)
11:28:34 - daemon.graph - INFO - [LLM] Tool call: bash — {'command': 'cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensembl..., tools: ['bash']
... (silence) ...
11:55:17 - daemon.services.job_processor - DEBUG - [TRACE] _process_loop: poll timeout, processing next job (...)
```

Observations:
- The LLM call at `11:28:29` is followed 5s later by the bash tool call at `11:28:34`.
- No `[LLM]` log line ever appears again — the agent node is never re-invoked.
- The `job_processor` keeps tracing every 30s, proving the main event loop is alive.
- No error or timeout is logged in the bash tool — suggesting `asyncio.wait_for` never fires the timeout (or the default 1800s timeout hasn't yet elapsed when the user noticed).
- No `ToolMessage` ever appears in any state — the tool result never reached the graph.

---

## Why This Matters

Agents (especially `developer` and `gaia`) **natively** run shell commands like:
- `nohup ./dev.sh > /tmp/log 2>&1 & echo $!`
- `python server.py &` to start a long-running dev server and continue working
- `(long_running_command &) && echo started`
- `cmd > /tmp/out.log 2>&1 &`

This is a very natural, common pattern for an autonomous coding agent. The current implementation **silently freezes the agent** whenever such a command is issued, which makes the bash tool essentially unsafe for any real development workflow.

The lack of any feedback (no timeout, no error, no log) makes this especially hard to diagnose.

---

## Reproduction

Minimal repro:

1. Start the daemon (any mode that uses the `developer` agent).
2. Ask the developer agent to start the dev server in the background.
3. Watch the logs.

**Expected:** bash tool returns in ~5s with the output, agent continues.
**Actual:** bash tool never returns. Agent node stops progressing. No further `[LLM]` log lines. Main event loop continues normally (unrelated services still run).

Triggering command (used in the original bug report):
```bash
nohup ./dev.sh > /tmp/ensemble-dev-migration.log 2>&1 &
echo "PID: $!"
sleep 5
cat /tmp/ensemble-dev-migration.log
```

---

## Suggested Fix Directions (not applied)

There are several possible directions; the right one will depend on how much we trust the agent and how much we want to be opinionated about its shell usage.

### Option A: Detect backgrounded commands and refuse / reframe
- If the command contains a `&` token (or matches a regex like `nohup .* &`), warn the user that the bash tool will hang on backgrounded subprocesses and suggest using the `instance`/`project` tools (or a dedicated `run_async` tool) instead.
- Could even strip the trailing `&` automatically and run synchronously.

### Option B: Force `setsid`/double-fork to fully detach the backgrounded child
- Wrap the command in `setsid bash -c '...' </dev/null >/dev/null 2>&1 &` to ensure the backgrounded process does not inherit the shell's stdio.
- Keeps the agent's UX natural but guarantees the parent shell exits.

### Option C: Use `start_new_session=True` and `preexec_fn=setsid` on the subprocess
- When the bash tool detects a `&` in the command, spawn the backgrounded portion under its own session, fully detached from the parent shell's stdio.

### Option D: Add a watchdog / tool-call timeout with explicit log
- Even if we cannot prevent the hang structurally, emit periodic `[Bash] still running (Xs elapsed)` logs and surface a `[Bash] timed out` error to the LLM so the agent is not silently frozen.
- This is the **minimum** improvement — at least the agent gets feedback and can recover.

### Option E: Provide a separate `start_background` tool
- Make it explicit: an `instance` or `process` tool that starts a backgrounded long-running process with proper detachment, log capture, and PID tracking.
- The bash tool's docstring would explicitly forbid `&` in commands.

### Recommendation

A combination of **(D) + (B)** is the smallest reasonable improvement:
- (D) ensures no silent freeze — agent always gets feedback within the configured timeout.
- (B) ensures the common `nohup ... &` pattern actually works (the dev-server-starting case the user hit).

For longer-term hygiene, also consider **(E)**: agents have a real need to start long-running dev servers, and forcing them through `bash` with a side-channel (file redirect + echo PID) is a workaround that should become a first-class tool.

---

## Workarounds for Users (until fixed)

If an agent must start a long-running process, use one of:

1. **Double-fork pattern** (manually detach from parent stdio):
   ```bash
   (nohup ./dev.sh > /tmp/ensemble-dev-migration.log 2>&1 < /dev/null &) && echo "Started"
   ```
2. **Use `setsid`:**
   ```bash
   setsid -f ./dev.sh > /tmp/ensemble-dev-migration.log 2>&1 < /dev/null
   ```
3. **Use the instance/project tools** to spawn a child agent with a separate context, or use the `jober` agent to manage long-running jobs.

Avoid bare `nohup ... &` patterns — they will hang the bash tool today.

---

## Related Files

| File | Lines | Role |
|------|-------|------|
| `daemon/tools/bash.py` | 38–111 | Async bash tool implementation |
| `daemon/graph.py` | 585 | `ToolNode(tools)` registration |
| `daemon/tools/instance.py` | 240–296 | `_make_workdir_aware` async wrapper (sets `coroutine=`) |
| `docs/architecture/concurrency-model.md` | 158–176 | Prior issue: sync `subprocess.run` blocking event loop (already fixed) |
| `dev.sh` | 1–68 | uvicorn launcher used in the repro command |

---

## Notes

- This is **not** a sync-lock issue on the bash tool itself. The `bash` function has no `asyncio.Lock` and uses `asyncio.create_subprocess_shell` with `await proc.communicate()`. Multiple bash calls can run concurrently without any internal serialization.
- The earlier "sync subprocess.run" bug documented in `docs/architecture/concurrency-model.md` was already fixed; this is a **different** category of issue (process-detachment, not event-loop blocking).
- The 20+ minute silence in the log is consistent with a stuck `await proc.communicate()` — the default bash tool timeout is 1800s (30 minutes), after which an `ERROR: Command timed out after 1800 seconds` should be returned. If the user had waited the full 30 minutes, the agent would have recovered with that error.
