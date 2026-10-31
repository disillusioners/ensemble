# Research: Bash-Tool Process Mechanics & Orphan Escape Paths

Worktree: `agents-ensemble-wt-bash-tool-orphan-reaper` (branch `feature/bash-tool-orphan-reaper`, base `cbafcc8d9edf7d9be5b7216463903c109784cded`). All citations are from THIS tree. Read-only research; no code modified.

Incident correlation note (2026-10-31): the forensic fingerprint (fds 1/2 → UNLINKED `bash-stdout-*.tmp` / `bash-stderr-*.tmp`) matches the only producer of that naming — `daemon/tools/bash.py:740-741` (`tempfile.mkstemp`) combined with the unconditional unlink-in-finally at `daemon/tools/bash.py:1050-1056`. Unlinked-but-open files are exactly what a backgrounded grandchild holding inherited FDs leaves behind after the tool call returns and the finally block unlinks (the file-comment block at `bash.py:719-727` explicitly acknowledges "the grandchild holding the file FD open is harmless" — harmless for hangs, fatal for cleanup).

Prior art read: `.agents/shared/planning/auto-kill-bg-processes/` (plan-overview.md Rev 2 2026-07-18; phase1-plan.md; phase2-plan.md; phase3-plan.md; decisions.md). What SHIPPED from that plan is verified in §4 below; what was planned-but-relevant is flagged.

---

## 1. SPAWN MECHANICS

Entry point: `bash()` tool, `daemon/tools/bash.py:672-679`, registered under category `"bash"` via `@register_tool_category("bash")` (`bash.py:668`) and `@tool(args_schema=BashInputSchema)` (`bash.py:671`). Args schema: `command: str | List[str]`, `timeout` default 1800 (clamped 0–1800, `bash.py:682-686`), `workdir`, `input`, `env` (`bash.py:661-665`).

- **argv construction**: NO argv of its own and NO login shell.
  - `command` as **string** → `await asyncio.create_subprocess_shell(command, ...)` (`bash.py:794-802`) — i.e. the platform default shell in `sh -c` mode (asyncio `shell=True` semantics). Not `bash -c`, not a login shell; no `-l`/`-i` flags anywhere in the file.
  - `command` as **list** → `await asyncio.create_subprocess_exec(*command, ...)` (`bash.py:784-792`) — no shell interpretation at all.
- **cwd**: `cwd=workdir` passed to both spawn calls (`bash.py:789` exec, `bash.py:799` shell). `workdir=None` → child inherits the daemon process cwd (default asyncio/subprocess behavior; no fallback is set in the file).
- **env**: `assembled_env` starts `None`; if the caller passed `env`, it is built as `os.environ.copy()` ⊕ overlay, then run through `_resolve_secret_markers` and `_kms_resolve_env` (`bash.py:771-782`). When no overlay is passed, `env=None` reaches the spawn call (`bash.py:790/:800`) → the child inherits the FULL daemon environment (default subprocess semantics).
- **start_new_session**: `subproc_kwargs["start_new_session"] = True` on non-Windows (`bash.py:728-730`). Consequence (POSIX): the direct child calls `setsid()` at exec → it becomes **session leader AND process-group leader; its PGID == its PID**, and every descendant that does not change session/group inherits that PGID. The code's own fallback comment states this: `pgid = proc.pid  # start_new_session=True → PGID == PID` (`bash.py:822-823`).
- **stdout/stderr wiring to mkstemp files**: `tempfile.mkstemp(prefix="bash-stdout-", suffix=".tmp")` and `mkstemp(prefix="bash-stderr-", suffix=".tmp")` (`bash.py:740-741`); raw fds closed, reopened `w+b` (`bash.py:746-747`), passed as `stdout=stdout_file, stderr=stderr_file` (`bash.py:787-788` exec, `bash.py:797-798` shell). Temp FILES are a deliberate anti-hang design: a backgrounded grandchild inheriting the file FDs cannot hang `proc.wait()` the way an inherited pipe write-end would (`bash.py:719-727`). Parent closes its copies in the `finally` right after spawn (`bash.py:803-810`) and **unlinks both files in the function-level finally** (`bash.py:1050-1056`) — while a still-live grandchild keeps the now-unlinked inodes open (the incident fingerprint).
- **input handling**: if `input` is provided, it is written to `tempfile.mkstemp(prefix="bash-stdin-", suffix=".tmp")` and the file (opened `"rb"`) is passed as the child's `stdin` (`bash.py:751-756`); otherwise `stdin=asyncio.subprocess.DEVNULL` (`bash.py:759`).
- **What group/session does the direct child get**: its OWN new session and process group (session leader, PGID == PID), because of `start_new_session=True` (`bash.py:730`, `:822-823`). The daemon/parent stays outside that session.
- **Where pgid is captured and registered**: AFTER spawn, guarded by `if instance_id is not None and proc is not None` (`bash.py:819`): `pgid = os.getpgid(proc.pid)` with `except (ProcessLookupError, OSError): pgid = proc.pid` (`bash.py:821-823`); then `await get_bash_process_registry().register(instance_id, proc.pid, pgid)` (`bash.py:832`). The local `pgid` variable is initialized `None` pre-try (`bash.py:696-701`) so the timeout/cancel arms can use the eager capture instead of re-deriving (`bash.py:697-700`, `:884-887`, `:1004-1006`). A second, richer stamp goes to the pid_registry with start_time jiffies + kind + sanitized command (`bash.py:830-831`, `:847-875`).

---

## 2. ESCAPE PATHS (processes killpg-of-tracked-pgid cannot reach)

The tracked handle is exactly one process group: `os.killpg(pgid, ...)` in `BashProcessRegistry._kill_group` (`bash.py:503-514`) and in `_kill_process` (`bash.py:611-625`). Anything outside that group at signal time is unreachable. Enumerated paths:

### Path A — NORMAL COMPLETION with backgrounded children (`cmd &`) — THE INCIDENT PATH
- Mechanism: with `start_new_session=True` the whole tree (shell + `cmd &` grandchild, absent job control) shares PGID == shell PID. When the shell exits 0, `await asyncio.wait_for(proc.wait(), ...)` returns (`bash.py:881-882`) and the tool returns EXIT CODE 0 (`bash.py:971-972`). **The tool performs NO killpg on the normal-completion path** — the success arm (`bash.py:926-957`) only stamps the pid_registry exit (`:933-939`) and **unregisters the BashProcessRegistry entry** (`:948-951`, the N-1 leak fix). Removing the entry is the last-cleanup-opportunity miss: from that moment no registry-based sweep (cleanup_instance / cleanup_all) can ever reach the grandchild again, even though it is still alive in the (now leaderless) group.
- The backgrounded grandchild is then reparented to launchd/init (PPID 1) when the shell dies — this happens at shell exit, independent of any daemon restart.
- Missed-cleanup site: **`bash.py:926-957`** (success arm: mark_exit + unregister, no `_kill_group` call). Compounded by Path F (RAM-only registry) for anything surviving a restart.
- `timeout=0` ("no timeout", `bash.py:879`) has the same tail with no timeout arm at all.
- Existing tests pin the *return-doesn't-hang* behavior of exactly this shape — `(sleep 10 &)` (`tests/test_tools.py:122-133`) and bare `nohup … &` (`tests/test_tools.py:135-173`) — i.e. the escape is currently asserted as CORRECT behavior.

### Path B — child calls `setsid()` / double-fork (`setsid` binary, `os.setsid()`, daemonize idiom)
- Mechanism: a descendant that creates a new session leaves the tracked group permanently; `killpg(shell_pgid)` never reaches it. `nohup` and `disown` do NOT do this (they only ignore SIGHUP / remove the shell job-table entry — PGID unchanged, still reachable); a real `setsid()`/`os.setsid()` does.
- Missed-cleanup site: the kill primitives themselves can only target the one group — `_kill_group` (`bash.py:503-514`) and `_kill_process` (`bash.py:604-625`) have no cross-session reach. Documented as a known limitation in `cleanup_all`'s docstring: "Truly-detached orphans whose child called `setsid` sit outside the original process group, so `killpg` cannot reach them" (`bash.py:480-486`), mirrored in the observer's Tier-1 comment (`daemon/services/job_feedback_observer.py:3301-3309`).
- Characterization test pinning this boundary: `tests/tools/test_auto_kill_integration.py:1675-1749+` (`TestSetsidOrphanSurvivesCleanup` — a Python `fork()+os.setsid()+execlp("sleep")` grandchild survives `cleanup_instance`'s killpg, by design).

### Path C — nested shells with job control (`set -m` / monitor mode)
- Mechanism: non-interactive `sh -c` keeps background jobs in the shell's group (Path A), but a script that enables job control (`set -m`) makes EACH background job its own process group (PGID = child PID ≠ shell PGID). The tracked pgid (the shell's) then covers only the shell; every `-m`-backgrounded job is a separate, untracked group. Same unreachable-by-killpg sites as Path B (`bash.py:503-514`, `:611-625`); nothing in the spawn (`bash.py:728-730`) or wait (`bash.py:880-901`) logic enumerates child groups.

### Path D — timeout path racing a self-detaching child
- Mechanism: on timeout, `_kill_process(proc, pgid=pgid)` runs SIGTERM-to-group → 5 s grace → SIGKILL-to-group (`bash.py:883-887` → `:611-625`). Membership is evaluated at SIGNAL time: a child that calls `setsid()`/`os.setsid()` between spawn and the TERM/KILL lands outside the group and survives both signals. The eager pgid capture (`bash.py:697-701`, `:821-823`) prevents PID-recycle mis-signaling but not group-escape.
- Missed-cleanup site: `bash.py:883-901` (timeout arm) — after `_kill_process` + unregister (`:889`) nothing re-checks for surviving group members; a grandchild that detached mid-grace is gone from all bookkeeping.

### Path E — spawn-time `instance_id=None` (untracked from birth)
- Mechanism: registration is gated on `instance_id is not None` (`bash.py:819`); the `None` case only logs `"bash: instance_id is None; skipping process registration"` (`bash.py:876-877`). Such a process is in NO registry — unreachable by any sweep. Production wiring injects instance_id via `_make_instance_id_aware` (`daemon/tools/instance.py:6295-6298`, injection at `:2454-2458`), so this is reachable only if `get_current_instance_id()` yields None or via direct coroutine callers (tests, chart lane) — but it is a concrete untracked-by-construction lane.

### Path F — daemon death (hard crash / SIGKILL) with live tracked groups; RAM-only registry
- Mechanism: the registry is a module-level singleton holding plain dicts (`bash.py:427-428`, singleton at `:517-525`); `cleanup_all`'s docstring states "this in-memory registry does not survive a daemon restart, so processes from a hard crash cannot be enumerated" (`bash.py:484-486`). Graceful shutdown DOES sweep (`daemon/manager.py:15229-15244`), but a crash/SIGKILL (or any non-graceful stop) leaves tracked groups alive with no bookkeeping on next boot.
- Missed-cleanup site: nothing persists `(instance_id, pid, pgid)` for re-acquisition (see §3 — the DB lane exists but stores only the direct child's row and is not consulted by any reaper).

### Path G — cancellation landing in the spawn→register window with an already-exited shell
- Mechanism: `CancelledError` between `create_subprocess_*` (`bash.py:784-802`) and `register` (`bash.py:832`) IS handled (`bash.py:990-1030`: shield `_kill_process` at `:1006` with `pgid` still `None` → fallback `os.getpgid(proc.pid)` at `bash.py:604`), BUT if the shell already exited (fast `cmd &` pattern), `os.getpgid` raises and the `except OSError: pass` at `bash.py:629-630` swallows it → the same-group grandchild survives, entry never registered → same terminal state as Path A.

### Explicitly NOT escape paths (for the plan's completeness)
- Foreground long command, no detach: reachable — timeout arm killpgs the group (`bash.py:887`, `:611-625`); pinned by `tests/test_tools.py:175-207+` (backgrounded `sleep 60 & …; wait` under timeout dies with the group).
- Same-group `nohup … &` / `disown` WITHOUT setsid: reachable by any later killpg of the tracked pgid *while the entry exists* — i.e. only before the success arm unregisters (Path A is what removes that reach), and pinned positively by `tests/tools/test_auto_kill_integration.py:514-622` (`nohup` grandchild killed via `_kill_group`).

---

## 3. IDENTITY INVENTORY

**Known at spawn time** (`bash.py:819-865`):
- child `pid` (`proc.pid`), eager `pgid` via `os.getpgid` with PID-fallback (`:821-823`); **session id: NEVER captured anywhere** — no `getsid` call exists in `bash.py`, and `BashProcessEntry` carries only `pid: int, pgid: int` (`bash.py:416-421`).
- Linux-only extras for the pid_registry: `/proc/<pid>/stat` field-22 starttime jiffies (`_read_proc_start_time`, `bash.py:830`; None on macOS/Windows per `:702-708`) and VmHWM (`:831`).

**Where tracked**:
- `BashProcessRegistry._entries: Dict[str(instance_id) → List[BashProcessEntry]]` under a single `asyncio.Lock` (`bash.py:427-436`).
- Scope: **module-level singleton** via `get_bash_process_registry()` (`bash.py:517-525`) — per-daemon-process, global to all instances.
- Secondary: the resource_monitor `PidRegistry` live side (in-RAM queue) stamps the same bash child with `pid/start_time/kind/instance_id/pgid/command/parent_pid` (`bash.py:847-865`; registry module docstring `daemon/services/resource_monitor/pid_registry.py:1-6`).

**Entry lifecycle (when entries are REMOVED)**:
- `unregister(instance_id, pid)` — explicit removal after `_kill_process`: timeout arm `bash.py:889`, success arm `bash.py:948-951` (the ":943 leak fix" the task references = N-1, comment block `bash.py:940-947`), CancelledError arm `bash.py:1009-1015`.
- `cleanup_instance(instance_id)` — atomic bucket pop then killpg each entry (`bash.py:460-475`).
- `cleanup_all()` — snapshot keys, pop per instance (`bash.py:477-501`).
- Normal completion is exactly the success arm above — i.e. removal WITHOUT kill is the designed behavior (§2 Path A).

**Durability**: `BashProcessRegistry` is **RAM-only, confirmed** — plain dict state (`bash.py:428`), no file/DB write anywhere in the class; the restart loss is self-documented (`bash.py:484-486`) and echoed at `job_feedback_observer.py:3305-3309`.
- The ONLY durable identity trail is the separate `resource_pid_map` DB table (flushed asynchronously from the in-RAM PidRegistry — `pid_registry.py:1-6`; flusher writes the table at `daemon/services/resource_monitor/flusher.py:462` and `:811`). Schema: `daemon/models/resource_monitor.py:244-296` — `pid` (:280-283), **`pgid` (:284)**, `start_time` jiffies (:285-288), `instance_id` (:289), `agent_id` (:290), `kind` (:291-292), plus spawned/exited/exit_code/exit_kind columns; partial UNIQUE `(pid) WHERE exited_at IS NULL` (:263-269); DORMANT v2 `action/action_by/acted_at` columns reserved for a future control seam (:251-253). Retention: exited rows swept after 7 days; LIVE rows (`exited_at IS NULL`) are never swept (`flusher.py:171-177`).
- Design-relevant caveat: this durable lane rows cover ONLY the direct child shell — grandchildren are never stamped, and on normal completion the shell row is exit-stamped (`exit_kind='success'`, `bash.py:933-939`) even while group members live; the row's `pgid` remains the only durable pointer to the still-inhabited group.

---

## 4. CLEANUP HOOK INVENTORY (what exists in THIS tree)

(a) **Timeout path** — `bash.py:883-901`: `_kill_process(proc, pgid=pgid)` (`:887` → SIGTERM group `:612` → 5 s wait `:618` → SIGKILL group `:623`, all `except OSError: pass`-guarded), then `unregister` (`:889`) and pid_registry `mark_exit(EXIT_KIND_TIMEOUT)` (`:894-900`).

(b) **CancelledError path** — `bash.py:990-1030`: `task.uncancel()` (`:995-997`), `await asyncio.shield(_kill_process(proc, pgid=pgid))` (`:1006`), shielded `unregister` (`:1009-1015`), `mark_exit(EXIT_KIND_CANCELLED)` (`:1020-1027`), always re-raise (`:1030`). (This is the phase2-plan "C1 leak fix" — SHIPPED.)

(c) **Normal-completion path** — **NO killpg at all**: `bash.py:926-957` = pid_registry `mark_exit(EXIT_KIND_SUCCESS)` (`:933-939`) + `unregister` (`:948-951`). (The N-1 unregister is SHIPPED; a kill-on-success has never existed.)

(d) **Instance terminal transitions → cleanup_instance** (SHIPPED, both tiers):
- COMPLETED/ERROR/FAILED: two-tier block inside `_dispatch_instance_post_commit_side_effects` (`daemon/services/job_feedback_observer.py:3286-3379+`): Tier-1 proc `cleanup_instance(instance_id)` (`:3328-3335`), Tier-1 bash `cleanup_instance(instance_id)` (`:3337-3356`), Tier-2 root-gated (`parent_id is None`) descendant sweep — `tree_ids = await self._cleanup_descendants_of(instance_id)` (`:3362-3363`) then per-descendant proc `:3369-3375` and bash `cleanup_instance(iid)` (`:3377-3379+`). Known-limitations comment mirrors `setsid`/crash gaps (`:3301-3309`).
- TERMINATED (terminate/cascade): step 2.55 proc cleanup (`daemon/services/instance_lifecycle.py:2770-2780`) and step 2.56 bash cleanup (`:2782-2794`) in the terminate flow ("TERMINATED instances leak bash-spawned process groups until root finalizes or daemon shutdown" — `:2784-2786`).
- Chart lane: `_bash_sweep_render_tree(child_id)` belt-and-braces `cleanup_instance` after chart-render kill (`daemon/tools/chart_tools.py:663-679`).
- PAUSE: no cleanup (pause-first/quiesce lifecycle touches no registry — consistent with the lifecycle invariants in the Core Architecture blueprint).

(e) **Daemon shutdown → cleanup_all** (SHIPPED): `InstanceManager.shutdown()` calls proc `cleanup_all()` (`daemon/manager.py:15207-15227`) then bash `get_bash_process_registry().cleanup_all()` (`:15229-15244`), both best-effort try/except, BEFORE the step list (`:15246+`). Caller chain: uvicorn shutdown → FastAPI lifespan shutdown → `await manager.shutdown()` (`daemon/api.py:3857-3858`; corroborated by `daemon/__main__.py:508-518` comments). No signal handler calls it directly.

(f) **Boot sweep (adjacent shipped prior art, NOT bash-registry-based)**: `sweep_stale_subprocesses()` runs in `main()` before config/uvicorn (`daemon/__main__.py:389-399`, fail-open). Predicate (ALL required): same cgroup as THIS daemon, starttime strictly OLDER than the daemon's, not self/ancestor/descendant, not kernel-thread/zombie (`daemon/services/boot_subprocess_sweep.py:28-48`); TERM → bounded grace → SIGKILL with pre-signal PID-recycle re-verification (:50-61); kill-switch `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` lane-conditional — default ON only in the prod frozen lane (`sys.frozen`), OFF in dev/test/manual (:63-98, `:120-130`, `is_prod_lane` `:140-180`). Scope limits: boot-time ONLY, same-cgroup ONLY, prior-incarnation ONLY. It does not consult `BashProcessRegistry` or `resource_pid_map`, and its predicate is defined in Linux `/proc` + cgroup-v2 terms (docstring `:1-26`, `:31-41`) — on the macOS prod host (no `/proc`; cf. the project critical note that the resource_monitor producer was dead on macOS for exactly this reason) it cannot enumerate candidates (inference from the `/proc`-defined predicate, marked as inference: no explicit platform guard was found in the module).

**Summary of shipped-vs-planned**: everything in Rev-2 Phases 1–2 (proc+bash registries, eager pgid, CancelledError fix, Tier-1/Tier-2 wiring, both `cleanup_all`s) is SHIPPED in this tree; Phase-3 integration tests exist (`tests/tools/test_auto_kill_integration.py`). Nothing in the shipped design reaps (1) normal-completion stragglers (Path A), (2) setsid/job-control escapees (Paths B/C), or (3) post-crash orphans on a non-Linux host (Path F) — those are the open gaps a reaper must own.

---

## 5. INTEGRATION SURFACE for a future reaper

- `daemon/tools/_tool_registry.py` is **documentation/metadata-only**: `_full_docs` + `_tool_metadata` dicts (`_tool_registry.py:14-18`) plus the `DYNAMIC_TOOL_NAMES` frozenset (:23+). `@register_tool_category("bash")` (`bash.py:668`) attaches category/doc metadata. **No lifecycle or per-instance teardown hooks exist in the registry** — it cannot host a reaper.
- instance_id injection seam: `_make_instance_id_aware` wraps the bash tool at instance-tool assembly (`daemon/tools/instance.py:6295-6298` — layered over `_make_workdir_aware`; injection logic `:2454-2458`). This is where ownership metadata enters the tool; a reaper needs no change here (the registry is already instance-keyed).
- Reachable seams for reaper triggers, all verified in §4: terminal dispatcher `job_feedback_observer.py:3314-3379` (per-terminal + root tree sweep), terminate path `instance_lifecycle.py:2770-2794`, shutdown `manager.py:15207-15244` via `api.py:3858`, boot `__main__.py:389-399`. There is **NO turn-end hook** anywhere: nothing runs at graph-turn end against the bash registry (grep over `daemon/` for `get_bash_process_registry` shows only: bash.py itself, manager shutdown, lifecycle terminate, observer tiers, chart_tools child sweep). A turn-end/periodic reaper would be a NEW seam; the closest existing periodic-service pattern to copy is the boot sweep's fail-open + kill-switch + bounded-pass shape (`boot_subprocess_sweep.py:57-98`) or `JobLockSweepService`'s interval service.
- Dormant control seam: `resource_pid_map` already carries reserved `action`/`action_by`/`acted_at` columns "so P6 (control seam) does NOT re-create schema" (`daemon/models/resource_monitor.py:251-253`) — the intended landing zone for DB-driven reaper state.

---

## 6. TESTS pinning bash-tool process behavior (extend, don't duplicate)

- `tests/test_tools.py`
  - `:122-133` — `(sleep 10 & echo done)` returns immediately with EXIT CODE 0 (pins normal-completion-with-backgrounded-child as no-hang; implicitly pins the Path-A no-kill behavior).
  - `:135-173` — bare `nohup … &` returns immediately (pins the temp-file FD design, `bash.py:719-727` rationale).
  - `:175-207+` — `sleep 60 & echo $! > pid; wait` with timeout=2: asserts timeout error AND the backgrounded sleep is dead afterward (pins timeout-arm killpg reach into same-group grandchildren).
  - `:48-120` — timeout edge pins (0.5 s float, negative/over-max rejection, zero = no timeout).
- `tests/tools/test_auto_kill_integration.py`
  - `:116`, `:177-475` — Tier-1/Tier-2 per-terminal dispatch tests patching `daemon.tools.proc_tools.get_background_process_manager` + `daemon.tools.bash.get_bash_process_registry` (AsyncMock registries).
  - `:514-622` — REAL-process test: `nohup`-style grandchild (same group, no setsid) is killed by `BashProcessRegistry`'s real `os.killpg` via `_kill_group`.
  - `:1004+`, `:1260+`, `:1343+` — shutdown/tree-cleanup legs (POSIX-gated).
  - `:1675-1749+` — **m8 characterization**: fork+`os.setsid()` grandchild SURVIVES `cleanup_instance` killpg; the comment states it exists so "a future PR claims to fix this, this test will fail and force a re-evaluation" (`:1688-1690`) — a setsid-reaching reaper MUST update or supersede this test deliberately.
- `tests/job_queue/test_job_feedback_observer.py:2492-2640` — bash Tier-1/Tier-2 mirror tests of the observer dispatch (mocked registries).
- `tests/test_manager_shutdown.py:292-346` — three tests asserting `manager.shutdown()` invokes bash `cleanup_all()` (and failure isolation).
- `tests/test_resource_pid_registry.py`
  - `:275-312` — bash stamp lane + N-1 "zero-monotonic-growth": BOTH the pid_registry AND `BashProcessRegistry` return to zero after successful bash calls (pins the success-arm unregister — will need re-baselining if a kill-on-success lands).
  - `:341-417+` — mark_exit kind routing / ContextVar kind-hint pins (`_PID_KIND_HINT`).
- `tests/test_chart_tools.py:1423-1532` — four tests patching `daemon.tools.bash.get_bash_process_registry` for the chart child sweep (`chart_tools.py:663-679`).
- `tests/integration/test_service_tool_kill_site_exemption.py`
  - `:176-281` (K4/K5) — `BashProcessRegistry.cleanup_instance` kill-site exemption semantics (dead-pid ESRCH swallowed, `:287`, `:522`, `:544`).
  - `:747-753` — REAL bash victim spawned `start_new_session=True` (pid==pgid) reachable by the cascade's killpg; `:884` teardown killpg.
- `tests/test_opt_o8_stale_subprocess_sweep.py:1-22` — boot-sweep pin set: TERM→grace→KILL order, prior-incarnation detection, current-tree/ancestor exclusion, PID-recycle skip, kill-switch OFF = total no-op, sibling-cgroup exclusion, kernel-thread/zombie exclusion (fenced: `os.kill` hard-raises, synthetic /proc tree, `:58-77`).
- Adjacent family (service tools, not bash): `tests/integration/test_service_reconciliation_e2e.py:736-805` — record-and-forward `os.killpg` at the `service_tool_manager` seam asserting exactly ONE SIGKILL (a pattern reusable for reaper kill-site census tests).

---

## Confidence footnotes

- All file:line claims verified by direct reads of THIS worktree except: (i) the macOS no-`/proc` consequence for the boot sweep, which is inference from the module's `/proc`+cgroup-defined predicate (no explicit platform guard found) and the project critical note about resource_monitor on macOS; (ii) `asyncio.create_subprocess_shell` = `sh -c` semantics, which is stdlib behavior, not codebase code.
- Incident-mechanism attribution (Path A as THE incident path) is inference from the unlinked-FD fingerprint + code structure; the codebase contains no record of the specific Oct-29 commands.
