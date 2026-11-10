# Phase 4: Proc Lane Enforcement

## Objective

Wire the Phase 2 guard into the **proc tool** (`BackgroundProcessManager`) so that a
proc execution whose process tree exceeds the configured ceiling is terminated
tree-wide, the manager's status reflects the RAM kill, and the caller receives a
typed `ram_limit_exceeded` error. The proc lane's existing watchdog/task machinery
is the natural host.

**Note the coverage asymmetry:** unlike bash, the proc tool writes **no durable
registry row** today — only `daemon/tools/bash.py:414` and
`daemon/tools/mcp/stdio_wrapper.py:783` touch `pid_registry`, and `proc_run` touches
neither the `bash_spawn_registry` nor the `pid_registry`. Under a RAM-only
substrate this is a non-issue; under a durable substrate (DQ5/DQ10) it is a **new
write path** and is scoped in Phase 7.

## Touch points (verified anchors)

| Seam | Location | What changes |
|---|---|---|
| S7 — post-spawn task set | `daemon/tools/proc_tools.py:1226-1245` | The RAM watchdog becomes the **4th task** alongside `reader_task`, `exit_task`, `timeout_task` |
| S9 — the timeout-task pattern | `daemon/tools/proc_tools.py:1241-1245` (`info.timeout_task = asyncio.create_task(self._timeout_killer(info, …))`) | The precedent to mirror — including its cancellation in the cleanup tuple (`:1296-1301`) |
| S10 — status mapping | `daemon/tools/proc_tools.py:786-801` (`_drain_exit_code`), `:855-875` (status mapping) | A RAM kill must map to an authoritative status (like `timed_out`/`user_stopped` ⇒ `"killed"`), and the manager must expose that it was a **RAM** kill, not a user stop |
| S11 — kill delivery | `daemon/tools/proc_tools.py:965-1086` (`_attempt_kill_signal`) | The 3-layer gate: status → liveness → **PID-ownership** (`/proc/<pid>/environ` exact-entry, `:1039-1052`; fail-open on unreadable) |
| Spawn-window cleanup | `daemon/tools/proc_tools.py:1247-1308` | The existing spawn-window re-check that kills an orphan if the process was cleaned up mid-spawn — the new task must be included in that cancellation tuple |
| Instance cleanup | `daemon/tools/proc_tools.py:1585-1667` (`cleanup_instance`), `:1669-1717` (`cleanup_all`) | The new task must be cancelled on both paths (no dangling task referencing a dead process) |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Thread the ceiling into the proc lane**, runtime-injectable, defaulting to the configured value (same contract as the bash lane) | Phase 1 Task 7 | An explicit per-call ceiling is honoured; the default path is unchanged |
| 2 | **Honour the kill-switch** — OFF ⇒ no measurement, no registration, no writes, and **no extra task created** (the task set must be byte-identical to today's: reader + exit + timeout) | Phase 1 Task 7 | With the switch OFF, `info`'s task set contains exactly today's tasks; no guard task is created |
| 3 | **Register the spawn with the guard** at S7, using the pgid captured at spawn (`start_new_session=True` ⇒ PGID == PID, `proc_tools.py:1179-1181`) — never re-derived after the fact | Phase 2 Task 7 | The guard holds the pgid captured at spawn; no post-hoc `getpgid` race |
| 4 | **Create the RAM watchdog task** as the 4th task at S7, mirroring the `timeout_task` pattern: a named `asyncio.create_task(...)`, held on `info`, cancelled in the **same** cleanup tuples (`:1296-1301`, `cleanup_instance`, `cleanup_all`) | Task 3 | On instance cleanup and on the spawn-window orphan path, the task is cancelled — no dangling task; a test asserts no task survives cleanup |
| 5 | **Add the RAM-kill status** (S10). `timed_out`/`user_stopped` are already **authoritative** status inputs (`:855-875`); a RAM kill must be a third authoritative input, and the manager must distinguish it from a user stop (the caller must learn *why* it died) | Phase 1 Task 4 | A RAM-killed process reports the ruled status; a user-stopped process still reports the user-stop status — the two are distinguishable |
| 6 | **Deliver the kill via `_attempt_kill_signal`** (S11) so the **PID-ownership gate** applies — the correct defense against PID recycling, and Linux-only (matching the enforcement target). Where the ruling's ladder differs from the proc lane's polite/force ladder, the ownership gate is **still** applied | Phase 1 Task 4, Phase 4 Task 5 | A recycled-pid scenario aborts the kill (the existing `aborted_recycled` path) and is logged as a safety event |
| 7 | **Return the typed error to the caller** per the DQ6 ruling — `surface="proc.exec"` (or the ruled value), with `limit_bytes` / `observed_peak_bytes` / `pid` / `pgid`. The proc lane's idiom is `"Error: …"` strings and tools **never raise** (pinned by `tests/tools/test_proc_tools.py:53-55`) — the envelope must fit that idiom | Phase 1 Task 6 | The proc lane's return value carries the ruled envelope; the tool does not raise; the existing proc error-string contract test still passes |
| 8 | **Write the proc-lane tests**: (a) a real ballooning tree via `proc_run`; (b) an under-limit command unaffected; (c) the kill-switch OFF ⇒ no extra task; (d) the negative paths — a non-Linux platform guard, an unreadable `/proc`, the `MAX_PROCESSES_PER_INSTANCE = 10` cap (`proc_tools.py:98`, enforced `:1130-1142`) unaffected, the spawn-window orphan path unaffected | Tasks 1–7 | The ballooning test kills the whole tree and reports the RAM status; the cap and orphan paths are unchanged |

## Coupling

- **Tight with:** Phase 2 (the guard module), Phase 5 (the error shape + knob names),
  Phase 6 (the tests).
- **Independent of:** Phase 3 (the bash lane) — no shared wiring code.
- **Loose with:** Phase 7 (durability) — only if DQ10 ruled for a durable verdict,
  which for this lane means a **new write path**.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The new task is not cancelled on some exit path ⇒ a dangling task polls a dead pid forever | Medium | Medium | Task 4 requires the task in **every** cancellation tuple (spawn-window orphan, `cleanup_instance`, `cleanup_all`); a test asserts no task survives cleanup |
| The RAM status is conflated with a **user stop** (both ⇒ `"killed"`) | Medium | Medium | Task 5 makes the RAM flag authoritative **and** distinguishable; the caller-facing error differs between the two |
| The proc lane's polite/force ladder (SIGTERM → 5 s → SIGKILL, `proc_tools.py:1413-1441`) is too slow for a RAM emergency | High | Medium | The DQ4 ruling's short-grace ladder applies to this lane too, with the ownership gate preserved (Task 6) |
| `_attempt_kill_signal` is fail-**open** on an unreadable `/proc/<pid>/environ` (`:1052`) | Medium | Low | Accepted house behavior; the guard's own pgid capture + the verdict record bound the blast radius. Documented, not silently overridden |
| The proc lane's 10-process-per-instance cap interacts with a RAM-killed process still occupying a slot | Low | Medium | Task 8 asserts the cap's behavior is unchanged; the killed process's slot is released by the existing cleanup path |
| A `setsid` escapee inside a proc execution balloons unseen | Medium | Medium | The DQ3 ruling's setsid stance governs; the escapee remains covered by the orphan reaper's stamp sweep at cleanup points |

## Exit Criterion

- A proc execution that exceeds the injected ceiling is **terminated tree-wide**,
  reports the RAM status, and the caller receives the typed envelope.
- An under-limit proc execution is **byte-identical** to the base-commit behavior.
- The kill-switch OFF path creates **no extra task** and performs no measurement.
- The new task is cancelled on **every** cleanup path (spawn-window orphan,
  `cleanup_instance`, `cleanup_all`) — asserted.
- The proc-lane test pack passes with **net-new reds = 0** against the base commit.
