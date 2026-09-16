# Research: Instance-Lifecycle & Process Kill/Reap Sites (for `service` tool exemption proof)

Repo: agents-ensemble @ branch `feature/service-tool` (fresh from latest), 2026-09-14.
READ-ONLY investigation. All paths relative to repo root. Evidence = file:line.
`UNVERIFIED` marks anything not directly confirmed.

**Executive conclusion up front:** every process-kill primitive in the daemon is
**registry-scoped**: bash groups (`BashProcessRegistry`), proc-run handles
(`BackgroundProcessManager`), and the code-server PID (`VSCodeServerManager`).
There is **no** `/proc` walk, no ppid traversal, no `killpg(0)`, no
session-wide sweep. A process that detaches (double-fork + setsid, own session,
own pgid, stdio→files) and is **never registered** in any of the three
registries is unreachable by every kill site found — same precedent as
`upgrade_journal.spawn_executor` (daemon/tools/upgrade_journal.py:1010-1035),
which is *deliberately not registered in any teardown registry* and documents
that the child "must survive BOTH tool-harness teardown and daemon death"
(:1016-1021).

---

## (a) Kill-site inventory table

Mechanism key: **pgid** = `os.killpg` on a captured group; **pid** = bare
`os.kill`/`proc.kill` on a tracked pid. "Reaches detached setsid proc?" = a
service process that was spawned, then did double-fork+setsid (left its spawn
group), or was never registered.

| # | Site (file:line) | Trigger | Mechanism | Signal(s) | Reaches detached setsid proc? |
|---|------------------|---------|-----------|-----------|-------------------------------|
| K1 | daemon/tools/bash.py:168 | bash tool timeout (`asyncio.TimeoutError` on `proc.wait()`, bash.py:329-333) | pgid (captured at spawn, bash.py:318-321; `start_new_session=True` bash.py:257-258) | SIGTERM → 5s → SIGKILL (:168-184) | **No** — killpg targets the *bash command's* group; a setsid'd service is in a different session/group. Also requires the service to be a descendant of *this specific bash invocation's* group. |
| K2 | daemon/tools/bash.py:179 | same as K1 (escalation after 5s grace) | pgid | SIGKILL | No (same reasoning as K1). |
| K3 | daemon/tools/bash.py:385 (via `_kill_process` :138-186) | `asyncio.CancelledError` inside bash tool (graph_task.cancel from pause/terminate/stop; cancellation propagates into `await proc.wait()` bash.py:327) | pgid, `asyncio.shield`-wrapped (:385), then registry `unregister` (:388-394) | SIGTERM→5s→SIGKILL | No — same group-scope argument; requires registry/pkid of this bash call. |
| K4 | daemon/tools/bash.py:101 (`_kill_group`, via `cleanup_instance` :54-69) | instance terminate cascade → bash registry cleanup (instance_lifecycle.py:2347-2354) | pgid list per instance (registry entries registered at bash.py:315-321) | SIGKILL | No — only kills groups captured from bash-tool spawns of *this instance_id*. A service never registered → not in the list. A setsid'd child already left any captured group. |
| K5 | daemon/tools/bash.py:71-95 (`cleanup_all`) | daemon graceful shutdown (manager.py:10940-10952) | per-instance walk → K4 | SIGKILL | No — in-memory registry only; docstring itself notes setsid orphans are unreachable (bash.py:74-79). |
| K6 | daemon/tools/proc_tools.py:1010 (`_attempt_kill_signal` :918-1035) | `proc_stop` tool (polite: SIGTERM :1368; `force=True`: SIGKILL :1343; escalation :1382); also `_timeout_killer` :829/:884 (proc_run timeout → SIGKILL) | pgid via `os.getpgid(proc.pid)`; PID-ownership verified 3-layer (tracking env `ENSEMBLE_PROC_TRACKING_ID` :118/:493, `/proc/<pid>/environ` :275, `ps eww` :355) before killing | SIGTERM, SIGKILL | **No.** Only fires for `process_id`s spawned+tracked by `BackgroundProcessManager` for the owning instance (spawn: :1042-1233, `start_new_session=True` :1133). Ownership check also fails-closed for foreign PIDs. |
| K7 | daemon/tools/proc_tools.py:1231-1232 | race guard in `start_process` (C2 re-check after spawn) | pgid | SIGKILL | No — same tracked-handle scope as K6. |
| K8 | daemon/tools/proc_tools.py:1537-1620 (`cleanup_instance`; SIGKILL at :1600) | instance terminate cascade → proc cleanup (instance_lifecycle.py:2330-2340); daemon shutdown via `cleanup_all` :1621-1670 (manager.py:10922-10935) | per-instance bucket pop → `_attempt_kill_signal` SIGKILL | SIGKILL | No — bucket contains only `proc_run` handles this instance spawned. Docstring: setsid-detached grandchildren unreachable (:1630-1634). |
| K9 | daemon/services/vscode_server_manager.py:458 + :697 (`_signal_pid` :685-713) | code-server stop — daemon shutdown `vscode_manager.stop()` (api.py:1400-1406), editor settings changes | pgid captured at spawn (:297, spawn :314, `create_subprocess_exec`) with bare-pid fallback :707-711 | SIGTERM → `VSCODE_STOP_GRACE_S`=5s (constants.py:139; wait :462-464) → SIGKILL :471 | No — targets exactly the tracked code-server pid/pgid (state.pid/pgid from PID file or spawn). A service tool pid is never in `vscode.state`. |
| K10 | daemon/services/vscode_server_manager.py:443-451 | stop() of an *adopted* code-server (no subprocess handle, crash recovery `attach_existing` api.py:1063) | bare pid (`os.kill`) + liveness probe `os.kill(pid,0)` :448 | SIGTERM → 5s → SIGKILL | No — adopted pid only; that pid is code-server's, read from the vscode PID file. |
| K11 | daemon/services/vscode_server_manager.py:1165-1190 (`_kill_orphan`; killpg :1178, kill :1181/:1186) | `start()` failure — half-spawned code-server that never opened its port | pgid (re-derived `os.getpgid(process.pid)`) with `process.kill()` fallback | SIGKILL | No — scoped to the just-failed code-server spawn. |
| K12 | daemon/tools/bash.py:172 / :183 | K1/K3 on Windows only | bare pid (`send_signal`/`proc.kill()`) | SIGTERM / kill | No (and platform-irrelevant for darwin target). |
| K13 | git-diff & doc-commit `subprocess.run(..., timeout=...)` (daemon/services/git_diff_service.py:119; daemon/services/doc_commit_service.py:297/:318/:399/:474/:488/:509) | synchronous git operations timing out (Python kills the direct child on timeout) | direct child pid (no groups) | kill (via stdlib) | No — short-lived direct children; a detached service is never spawned by these. |
| — | daemon/tools/upgrade_journal.py:559 | liveness probe of upgrade executor | `os.kill(p, 0)` — **signal 0, no kill** | — | Read-only probe; not a kill site. |
| — | daemon/services/job_feedback_observer.py:2846 | comment documenting killpg semantics | — | — | Confirms killpg-scoped model ("reaps via os.killpg — they will not be killed here" for non-group members). |

**No hits anywhere in daemon/ for:** `waitpid` (asyncio `proc.wait()` is used),
`SIGHUP`, `killpg(0)`, `/proc`-child enumeration for killing, ppid-tree walks.
(Verification note: whole-`daemon/`-scope greps were unreliable in this session;
per-directory sweeps were run for `daemon/tools/`, `daemon/services/`,
`daemon/routers/` plus targeted reads of `manager.py`, `graph.py`, `api.py`,
`__main__.py`, `cancellation.py`. Root-level `SIGTERM/SIGKILL` grep returned
comment/config hits only: config.py:498-501, constants.py:139,
`__main__.py`:290-306.)

---

## (b) Per-path narratives

### 1. Instance termination (trigger: DELETE /instances, watchover 3-strike, jobs-management bulk)

Entry: `terminate_instance` (daemon/services/instance_lifecycle.py:2113).
Sequence of *process-relevant* steps:

1. Request-registry cancel — `cancel_by_instance` (:2252) → LLM-request
   cancellation tokens (`CancellationReason` values in daemon/cancellation.py:10-17;
   terminate path uses SESSION_TERMINATED/USER_STOPPED at call sites). Tokens
   cancel LLM calls, not OS processes.
2. **Graph task cancel, bounded** — `_graph_tasks.pop` + `graph_task.cancel()`
   (:2277, :2292), `wait_for(shield(task), 5.0)` (:2295). The CancelledError
   lands inside the in-flight tool coroutine → if it is a bash call, K3 fires
   (SIGTERM→SIGKILL of that command's group) then the error re-propagates
   (bash.py:369-395).
3. MCP close (:2324-2328).
4. **proc cleanup** — `get_background_process_manager().cleanup_instance(id)`
   (:2330-2340) → K8 SIGKILL of every tracked proc_run group.
5. **bash cleanup** — `get_bash_process_registry().cleanup_instance(id)`
   (:2342-2354) → K4 SIGKILL of every tracked bash group. Comment states the
   purpose: "TERMINATED instances leak bash-spawned process groups until root
   finalizes or daemon shutdown" (:2344-2346).
6. Facade: `manager.terminate_instance` (daemon/manager.py:9062) forwards to
   the lifecycle cascade; `cancel_graph_task` (manager.py:8968-9060) is a
   narrower variant (task.cancel only → K3-equivalent kill of in-flight bash
   via cancellation; no proc/bash registry cleanup). External callers route
   through manager facade (routers/instances.py:660; call-site census of
   `cancel_graph_task` not exhaustively traced — UNVERIFIED).

**Detached-service verdict:** termination kills only (a) the in-flight bash
group via task cancellation and (b) registry-listed groups. A registered-never
service is untouched.

### 2. Cancellation paths (pause vs terminate)

- `pause_instance_cascade` (instance_lifecycle.py:3030): per node —
  `cancel_by_instance(node, CancellationReason.USER_STOPPED)` (:3151-3154),
  `graph_task.cancel()` (:3159-3173). **No proc/bash registry cleanup** —
  proc_run background processes of a paused instance keep running (by design;
  pause preserves state for resume). An *in-flight bash call* IS killed: the
  CancelledError propagates into `bash`'s `await proc.wait()` and the
  `except asyncio.CancelledError` handler runs the shielded group kill
  (bash.py:369-395) before re-raising. `CancellationReason` enum:
  daemon/cancellation.py:10-17 (TIMEOUT, WATCHDOG_RETRY, MANUAL, SHUTDOWN,
  SESSION_TERMINATED, USER_STOPPED) — reason affects LLM-token + SSE semantics
  only; the process-kill behavior is identical (bash K3).
- Pause-cancelled graph tasks stay PROCESSING with `CancellationReason`
  discriminating pause from shutdown (blueprint convention; pause-first-then-
  quiesce: `pause_instance_cascade` FIRST, `graph_task.cancel()`, LangGraph
  checkpoints at node boundaries, resume is DB-only).

### 3. Daemon shutdown

Chain: SIGTERM/SIGINT handled by uvicorn (`__main__.py`:290-293);
`timeout_graceful_shutdown` bounds only the connection/task-drain phase
(:297-309); lifespan shutdown then runs: live-hub (:1289), notification
broadcaster (:1293), WAITING_CHILDREN watchdog cancel (:1328-1347),
EligiblePendingSweep stop (:1379), **`vscode_manager.stop()`** (:1400-1406 →
K9/K10), **`manager.shutdown()`** (:1409).

`manager.shutdown` (manager.py:10894-10981) process-relevant order:
1. warmup task + background asyncio tasks cancelled (:10901-10913) — asyncio
   only, no OS kills.
2. **`get_background_process_manager().cleanup_all()`** (:10922-10935) → K8
   for every instance bucket.
3. **`get_bash_process_registry().cleanup_all()`** (:10937-10952) → K5.
4. `stop_sources` → `_cancel_all_active_requests` (SHUTDOWN reason,
   :10983-10985) → `_wait_for_inflight` → worker pool → event bus →
   maintenance → DB pools → checkpointer → MCP → opencode registry
   (`_shutdown_opencode_registry` :10987-10994: stops session managers,
   clears map, disposes engine — no OS kill documented).
5. Hard backstop: launcher `SIGKILL` of the daemon process itself after
   CHILD_STOP_WAIT_S=70s (`__main__.py`:304-306; scripts/stop-ensemble.sh
   WAIT_S mirrors it). This kills the daemon PID only — no child enumeration.

**Detached-service verdict:** every shutdown kill is (pgid|pid)-targeted at
registry-tracked processes. Nothing walks /proc, nothing signals the daemon's
own process group, no `killpg(0)`. Note: a detached service created *before*
setsid ran (window between spawn and setsid) would still be in the spawned
group and could be caught IF it were also registered — double-fork from the
first child closes this window. If `launcher.sh`/`stop-ensemble.sh` signal the
daemon's process group (UNVERIFIED — scripts not read in this pass), children
still in the daemon's group would die; setsid'd services would not.

### 4. proc_run children on terminate / daemon restart; proc_stop mechanics

- **Instance terminate** → killed: `cleanup_instance` SIGKILLs each tracked
  group (instance_lifecycle.py:2330-2340 → proc_tools.py:1537-1620).
- **Daemon graceful shutdown** → killed: `cleanup_all` (manager.py:10922-10935).
- **Daemon crash / hard SIGKILL / restart** → **orphaned, NOT reaped, NOT
  re-adopted** (except code-server): both registries are in-memory singletons
  (bash.py:111-119, proc_tools.py:1838-1845); docstrings state the crash-
  recovery leak explicitly (bash.py:76-79; proc_tools.py:1629-1634). Orphans
  reparent to init/launchd and keep running. `VSCodeServerManager.
  attach_existing()` (api.py:1051-1063) is the sole re-adoption path, via PID
  file, adopted at boot (:1063); adopted code-servers get a watchdog and are
  killed via K10 on stop.
- **proc_stop mechanics** (proc_tools.py:1264-1400+): polite stop = SIGTERM to
  the process group (:1368, via `_attempt_kill_signal` → `os.killpg
  (os.getpgid(proc.pid), sig)` :1010; Windows: `send_signal` :1020/:1035),
  wait `_STOP_GRACE_SECONDS`=5s (:106), escalate SIGKILL (:1382);
  `force=True` skips SIGTERM (:1340-1343). Pre-kill 3-layer PID-ownership
  verification (tracking env var read from /proc/environ or `ps eww`,
  :214-360, :493) prevents killing a recycled PID.

### 5. Instance GC / sweeps — none touch OS processes

- `_cleanup_cached_instances` TTL sweep (manager.py:4499-4585; 10-min cadence
  :4507; TTL `INSTANCE_CACHE_TTL_HOURS`=24h manager.py:143-144): releases
  in-memory graphs for terminal/PAUSED instances via `_release_cached_instance`
  (:4555 → :4203-4228) = `_cleanup_instance_state` (:4215; pops RAM dicts only,
  manager.py:3933-4022) + `cancel_by_instance(SESSION_TERMINATED)` (:4225-4228)
  → **no proc/bash registry cleanup, no OS kill**. Also evicts idle opencode
  session managers (:4564-4571) and sweeps stale injection queues (:4578-4581,
  `_cleanup_stale_injections` :4092+).
- `_cleanup_stale_completions` (manager.py:2591, scheduled :2359): CompletionRegistry
  RAM only.
- `EligiblePendingSweepService` (api.py:611-649, stop :1379): PENDING
  report-injection/task rows (DB) — no kill/spawn primitives in the service
  file (per-directory grep).
- `orphan_watcher_sweep.py`: DB watcher rows — no kill primitives found.
- WAITING_CHILDREN hang watchdog (api.py:708-782): detects stuck parents;
  any hard action would route through the ordinary cascade (indirect only;
  direct behavior not read — UNVERIFIED).

### 6. Global scan — all signal/kill call sites in daemon/ (completeness check)

| Primitive | Sites |
|-----------|-------|
| `os.killpg` | bash.py:101,168,179; proc_tools.py:1010,1231; vscode_server_manager.py:697,1178 |
| `os.kill` | vscode_server_manager.py:444,448(signal 0),449,525,536,606(signal 0),711; upgrade_journal.py:559(signal 0) |
| `send_signal` | bash.py:172 (win); proc_tools.py:1020,1035 (win fallback) |
| `proc.kill()` / `process.kill()` | bash.py:183 (win); vscode_server_manager.py:1181,1186 |
| `subprocess.run(timeout=)` implicit child kill | git_diff_service.py:119; doc_commit_service.py:297,318,399,474,488,509; vscode_server_manager.py:1272 |
| SIG constants (config) | constants.py:139 (`VSCODE_STOP_GRACE_S`=5); proc_tools.py:106 (`_STOP_GRACE_SECONDS`=5) |
| `waitpid` / `SIGHUP` | none found |
| `killpg(0)`, `/proc` child-walk, ppid traversal | none found |

Caveat (method, not content): repo-wide greps over the full `daemon/` scope
behaved inconsistently this session; the table is assembled from
per-directory sweeps (`daemon/tools/`, `daemon/services/`, `daemon/routers/`)
and targeted reads (`manager.py`, `graph.py`, `api.py`, `__main__.py`,
`cancellation.py`, `vscode_proxy.py`). `daemon/routers/` has no direct kill
primitives — all termination flows route to manager/lifecycle. `daemon/
sources/`, `daemon/clients/`, `daemon/repositories/` not individually swept
(UNVERIFIED; no architectural reason for kill primitives there — they are
HTTP/DB layers).

---

## Exemption proof sketch for the planned `service` tool

1. **Spawn unregistered** — model on `upgrade_journal.spawn_executor`
   (upgrade_journal.py:1010-1035): `Popen(start_new_session=True, close_fds=True,
   stdin=DEVNULL, stdout/stderr→log file)`, never inserted into
   `BashProcessRegistry` / `BackgroundProcessManager` / any PID file. K1-K11
   all require registry/PID-file membership → unreachable.
2. **setsid via start_new_session** — service leaves any ancestor group, so
   even a hypothetical group-level kill of a spawning bash command (K1-K3)
   cannot reach it; the K1/K3 exposure window exists only between spawn and
   setsid, which `start_new_session=True` at spawn eliminates (no window).
3. **Reparent to launchd** — follows automatically from double-fork+setsid;
   daemon death (graceful or SIGKILL) then has no handle and no enumerator
   that could find the process (no /proc scanning exists anywhere).
4. **Restart semantics** — no re-adoption path exists (only code-server's PID
   file); a service tool needs its own contract if re-adoption is desired.
5. **Watch items** (from this inventory): (a) keep the service out of any
   future "cleanup_all" sweeps (they are the aggregate kill entry points,
   manager.py:10922-10952); (b) the bash/proc timeout killers key on tracked
   handles — never route service control through the `bash` tool's group
   (i.e., don't launch the service via `bash` without detachment, or K1's
   30-min timeout SIGKILL applies to the whole group).
