# Research: Restart-Lane Map & Reaper Design Patterns (bash-tool orphan reaper)

- **Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-bash-tool-orphan-reaper` (branch `feature/bash-tool-orphan-reaper`, base `cbafcc8d9`)
- **Scope:** read-only research for the planning cycle. All `file:line` citations are from THIS tree.
- **Method note:** line numbers below were read directly from this worktree on 2026-10-31.

---

## 1. RESTART LANE MAP (end-to-end)

### 1.1 Arm → drain → spawn chain

1. **`system_restart` tool (arm only — never spawns):** `daemon/tools/upgrade_tools.py:2670-3117` (`async def system_restart` at :2670). The tool gates, validates, and records a durable `pending_op` in the upgrade journal, then registers an in-memory marker. Docstring: "arm → return → poll. LIVE is provenance-gated" (`upgrade_tools.py:136`). The tools module has an explicit **no-direct-process-spawn discipline** — the static test `test_static_no_direct_process_spawn_in_tools_module` is cited at `daemon/tools/upgrade_journal.py:2202-2204`.
2. **Post-turn callback (primary trigger):** the marker set by the actor tools is consumed at EXACT turn-end by the post-graph path: `daemon/services/instance_messaging.py:2099` (drain wrapped in `asyncio.shield` at turn end), `daemon/services/instance_messaging.py:5710` ("The marker set by the actor tools fires the daemonized executor"), `daemon/manager.py:1862` ("pops it at exact turn-end and fires the daemonized executor").
3. **Drain (the ONLY sanctioned spawn seam):** `InstanceManager.drain_pending_system_execution` — `daemon/manager.py:5443`. It pops the marker (:5472), builds argv for restart as `["bash", str(scripts_dir / "restart.sh"), env, "--run-id", run_id]` (:5496-5500), optionally adds `--reason` (:5501-5502), mints `ENSEMBLE_RESTART_LIVE=1` only when the arm-time provenance marker is present (:5514-5515; "absent ⇒ nothing is minted and restart.sh's `require_live_guard_restart` exits 78 fail-closed" :5506-5511). A pre-spawn guard refuses to spawn unless the durable pending_op exists with a MATCHING run_id (:5561-5575, R-M5-2/M-2 binding).
4. **`spawn_executor` — the daemonizer:** `daemon/tools/upgrade_journal.py:2865-3103`. Called from the drain at `daemon/manager.py:5592-5605` with `run_id` and kind-scoped `unit_prefix=UNIT_NAME_PREFIX_RESTART` (`"ensemble-restart-"`, `upgrade_journal.py:2492`). THREE branches (`upgrade_journal.py:2874-2912`):
   - **Branch 1 — Linux + systemd:** payload runs inside a transient SERVICE unit (`systemd-run --user --unit=ensemble-restart-<run_id> --wait --collect …`), so it "has neither the daemon's pgid lineage nor its cgroup — it survives BOTH tool-harness teardown AND daemon death BY CONSTRUCTION" (:2880-2883). The `Popen` child is only the `systemd-run --wait` CLIENT with `start_new_session=False` (:3007-3010). Env is threaded into the unit verbatim via `--setenv` (:2887, :2966-2970, ruling R3).
   - **Branch 2 — legacy (non-Linux / no systemd; macOS prod):** `subprocess.Popen(argv, stdin=DEVNULL, stdout=log_fh, stderr=STDOUT, cwd=str(install_dir), env=env, start_new_session=True, close_fds=True)` — `upgrade_journal.py:3092-3102`, comment at :3100: "≡ setsid: detaches the process group". Returns mode note `"(daemonized, start_new_session)"` (:3103). This is the macOS-relevant branch: **own session, PGID == child PID, and after the daemon exits the child is re-parented to PID 1 (launchd)**.
   - **Branch 3 — unit-managed host, transient start unavailable:** LOUD refusal (`ExecutorSystemdUnavailable`, :2950-2951 / :2845-2862) — never a silent fallback.
5. **Executor payload:** `scripts/upgrade/restart.sh` — fired by the post-turn callback (primary) or manual/fallback invocation where "the journal pending_op is the durable authority and this script completes it; boot sweep converges the rest, D-FA5.4" (`restart.sh:6-11`). Sequence (:13-37): preflight (env resolve + live guard + journal/pending_op validation + lock ADOPTION, :14-16 and :87-105) → grace wait (:216-220) → stop via `stop_via_stop_script` (stop-ensemble.sh; :223-228; "NO raw kills anywhere in this script: the stop is ALWAYS stop-ensemble.sh" :58-59) → start via `restart_via_launcher` (:230-241) → `/livez` + `/readyz` + version-identity gate (:243-291) → journal close (:35).
6. **`restart_via_launcher` — the launcher re-exec (protected nohup fallback lane):** `scripts/upgrade/lib.sh:4985-5180`. Supervision-aware hand-back: `UNIT_MANAGED` → `_supervision_handback_unit` (:5070-5074); `SCOPE_SURVIVOR` → scope-to-unit (:5075+); explicit-unit-on-incapable-host REFUSES (:5064-5066, "never silent-degrade to the nohup lineage"); everything else → the legacy nohup path, `lib.sh:5179`: `( cd "$INSTALL_DIR" && nohup ./launcher.sh >> data/launcher.log 2>&1 & )` + log line :5180. **The nohup'd launcher inherits the executor's (already allowlist-scrubbed) environment** — nothing downstream re-widens it.
7. **`launcher.sh` — production supervisor wrapper:** runs UNDER launchd/systemd; header: "No TTY, no shell env — every path is absolute" (`launcher.sh:3-6`). Env precedence: `INSTALL_DIR/.env` is exported HERE before the binary runs (`launcher.sh:1645-1649`). So the NEW daemon's environment comes from the staged `.env` + the executor's scrubbed env — structurally not from any bash-tool child.

### 1.2 Process attributes (legacy branch, macOS)

- New session + new process group via `start_new_session=True` (`upgrade_journal.py:3100`); PGID == PID at spawn.
- Deliberately NOT registered in `BashProcessRegistry` or any teardown registry: "the executor must survive BOTH tool-harness teardown AND daemon death" (`upgrade_journal.py:2913-2915`; design spec `self-restart-upgrade-phase2/decisions.md:179` and static-assertion target :280/:295).
- The daemon treats cancelling the reaper as safe by design: "the executor child leads its own process group via start_new_session=True and the OS reaps it" (`daemon/api.py:3807-3812`).
- Note on the "python3 fork+setsid" framing: the phase-2 design spec said "double-fork + os.setsid() + execv" (`self-restart-upgrade-phase2/phase2-plan.md:44`), implemented as `subprocess.Popen(..., start_new_session=True)` "≡ double-fork + setsid" (`self-restart-upgrade-phase2/decisions.md:179`) — same OS-level effect (new session, PPID→1 after parent death), no literal `os.fork()` in the tree.

### 1.3 What ENV the supervisor inherits (scrubbing)

- **`executor_env` allowlist** — `daemon/tools/upgrade_journal.py:2157-2188`: the executor gets ONLY `EXECUTOR_ENV_ALLOWLIST` (:2157-2173: `PATH, HOME, INSTALL_DIR, PORT, POSTGRES_DB, TMPDIR, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, DBUS_SYSTEM_BUS_ADDRESS, ENSEMBLE_PROMOTE_WATCHDOG`) + any `PG*`-prefixed var (:2174) + explicit per-call-site extras (:2186-2187). R-SR09 rationale: "NEVER the daemon's full environment (no .env passthrough, no API keys)" (:2139-2141). **Any `ENSEMBLE_*` var not in this list is stripped** — pinned by tests: `ENSEMBLE_RESTART_LIVE`/`ENSEMBLE_UPGRADE_LIVE` NOT in the allowlist (`tests/unit/tools/test_agent_restart.py:188-193`; `tests/unit/tools/test_promote_cgroup_survivorship_python.py:331-346`).
- **No `env -i` / operational `sudo` anywhere in `scripts/upgrade/`** — grep found zero `env -i` uses; `sudo` appears only in comments about root-owned staging (`scripts/upgrade/stage.sh:755, :775`). Scrubbing is allowlist-based in Python (`executor_env`) and structural in `launcher.sh` (.env loader, `launcher.sh:1645-1649`).
- **Extras merge is per-call-site, never ambient**: the drain adds `ENSEMBLE_RESTART_LIVE`/`ENSEMBLE_UPGRADE_LIVE`/`F2_VERIFIED_NOTE`/`ENSEMBLE_PROMOTE_WATCHDOG` explicitly (`daemon/manager.py:5514-5515, :5531, :5537-5553`; `upgrade_journal.py:2149-2155`).

### 1.4 Does ANY of it route through `daemon/tools/bash.py`?

**No.** The only spawn for the restart lane is `spawn_executor` called from `drain_pending_system_execution` (`daemon/manager.py:5592`); the bash tool is never in the chain (grep of `daemon/tools` for `system_restart` hits only `_tool_registry.py` and `upgrade_tools.py`; `bash.py` has zero upgrade-lane references). The executor is explicitly NOT registered in `BashProcessRegistry` (`upgrade_journal.py:2913-2915`), which is what makes it survive bash-registry teardown and daemon shutdown.

---

## 2. DISCRIMINATOR DESIGN SPACE

### 2.0 Direct prior art IN THIS TREE: `proc_tools` Layer-3 tracking token

`daemon/tools/proc_tools.py` (the `proc_run` tool) already implements the full **env-stamp + verify-before-kill** pattern:

- Stamp at spawn: `_TRACKING_ENV_VAR = "ENSEMBLE_PROC_TRACKING_ID"` (:131); `_build_child_env` starts from `os.environ.copy()` and injects the tracking id (:504-544, injection at :539), passed as the child `env=` (:1188, :1198/:1208).
- Verify before kill (PID-recycle defense): `_verify_pid_ownership` (:241-285) — Linux reads `/proc/{pid}/environ` (NUL-separated exact-entry match, :288-365); macOS/BSD uses `ps eww -p <pid>` (:368-503, invocation `["ps", "eww", "-p", str(pid)]` :427-433). Fail-open (`None` = cannot determine) vs fail-CLOSED (`False` = checked and NOT ours → abort) contract documented :394-417.

### 2.1 Candidate (a): env var `ENSEMBLE_BASH_CHILD=1` (inherited by descendants)

- **Feasibility:** the bash tool already assembles the child env — `os.environ.copy()` ⊕ tool-arg overlay (`daemon/tools/bash.py:771-782`), used at spawn (:784-802). Stamping here is a one-line change; all descendants inherit it (backgrounded `nohup foo &` grandchildren share the session/env; nothing in-tree strips child env).
- **Restart lane structurally cannot carry it:** the executor env is REBUILT from the allowlist (`upgrade_journal.py:2177-2188`) — an `ENSEMBLE_BASH_CHILD` value present in the daemon's environ is dropped before `Popen`/`--setenv`; the nohup launcher fallback inherits only that scrubbed env (`lib.sh:5179`); the next daemon gets env from `launcher.sh`'s `.env` export (`launcher.sh:1645-1649`). No code path re-injects unknown `ENSEMBLE_*` vars. Verified-arm extras are the ONLY widening and are per-call-site, pinned by tests (§1.3).
- **Fragilities found:**
  - **macOS env-readback is unreliable:** `ps eww` "only shows env vars for SOME processes… For ordinary CLI children (sleep, sh, simple Python) the env block is EMPTY" (`proc_tools.py:378-384`) — so on the prod macOS host, an env-stamp reaper often cannot VERIFY a candidate pid (fail-open). An env stamp alone cannot be the sole discriminator for a boot sweep that must kill *unknown* pids; it verifies positively only on Linux, or on macOS when env happens to be readable.
  - **`sudo`:** no operational sudo in the upgrade lane (grep: comments only, `stage.sh:755/:775`); if an agent ran `sudo`-prefixed commands through bash, env would be dropped by sudo's default env policy (not exercised anywhere in-tree).
  - **`sh -c` / re-exec:** plain shell re-exec preserves the environment; no repo code scrubs env on re-exec. `cron`/launchd-spawned grandchildren would NOT carry the stamp — but those lanes are not reachable from the bash tool's env inheritance.
  - **Indirect lane contamination:** a bash-tool child COULD invoke `bash scripts/upgrade/restart.sh …` or `nohup ./launcher.sh` directly. Restart.sh fails closed without the daemon-armed op (explicit target :93-97, `--run-id` :102-105, `require_live_guard_restart` :99 — exit 78, and `ENSEMBLE_RESTART_LIVE` is minted only by the drain `manager.py:5514-5515`), lock adoption (:39-45) and pending_op validation further gate it. A bash-spawned restart.sh WOULD live in the bash tool's process group (`start_new_session=True`, `bash.py:729-730`) and would already be in `BashProcessRegistry` (registration at :819-832) — so it is both killpg-reachable and registry-attributable while its group lives. Residual: if the daemon dies with the group intact, the registry rows survive only if DURABLE (candidate b).
  - **env scrubbing is elsewhere in the lane?** None that re-adds vars: `lib.sh` has no `env -i`; `resolve_env` only sets `INSTALL_DIR/PORT/POSTGRES_DB` from the staged `.env` (lib.sh ENV DISCIPLINE header :58-66); `launcher.sh` exports `.env` (:35-38) — a stamp could only reappear if someone wrote it into a staged `.env`, which is operator state, not bash-tool lineage.

### 2.2 Candidate (b): durable spawned-PID/pgid registry written BEFORE detach

- **Today's substrate is RAM-only and the code KNOWS it:** `BashProcessRegistry` eagerly captures pgid at spawn (comment "D4: eager PGID capture at spawn, BEFORE any await that could be cancelled" `bash.py:697-708`; `os.getpgid(proc.pid)` with fallback `pgid = proc.pid` :819-823; `register` :832). Its `cleanup_all` docstring names both gaps: "Truly-detached orphans whose child called setsid sit outside the original process group" (:481-483) and "crash-recovery leak: this in-memory registry does not survive a daemon restart, so processes from a hard crash cannot be enumerated" (:484-485).
- **Restart lane could never carry it:** registration happens ONLY in the bash tool (and the twin `proc_tools` bucket registered before spawn, `proc_tools.py:1160-1167`); `spawn_executor` deliberately skips every registry (`upgrade_journal.py:2913-2915`). There is no other writer.
- **Precedent for the row shape:** `ResourcePidMap` already models exactly this tuple — `pid, pgid, start_time (Linux jiffies), instance_id, kind, command, parent_pid, spawned_at (naive-UTC TEXT), exited_at, exit_kind` (`daemon/models/resource_monitor.py:244-315`), with a dialect-neutral partial UNIQUE over live rows (`uq_resource_pid_map_pid_active`, `WHERE exited_at IS NULL`, :258-276; precedent `daemon/repositories/service_tool/models.py:142-166`). CAVEAT: the resource pipeline persists only EXIT history — the live map is RAM (`daemon/services/resource_monitor/pid_registry.py:1-54`: live dict + history queue flushed on `mark_exit`), so `resource_pid_map` as-is is NOT a durable live registry; the reaper needs its own durable write at spawn time.

### 2.3 Candidate (c): process-group/session lineage

- Bash children lead their OWN session+pgid (`start_new_session=True`, `bash.py:728-730`; PGID==PID fallback :823). The restart lane CANNOT share it: the legacy executor gets its own NEW session (`upgrade_journal.py:3100`) and the systemd-branch payload has "neither the daemon's pgid lineage nor its cgroup" (:2880-2883).
- Lineage alone is not enumerable after orphaning: once the daemon (session leader) dies, descendants re-parent to PID 1 and the pgid survives only if you already KNOW the pgid — i.e. lineage supports the KILL mechanism (`os.killpg`, `bash.py:503-514`; SIGTERM→SIGKILL ladder `_kill_process` :582-631) but the identity must come from the registry (b). Children that themselves `setsid` escape any group (documented limitation `bash.py:481-483`; phase-1 lesson `self-restart-upgrade-phase2/phase2-plan.md:24`). Prior-art planning flagged this: "Truly detached orphan (child called setsid) — known limitation, unreachable by killpg" (`.agents/shared/planning/auto-kill-bg-processes/plan-overview.md:122`).

### 2.4 Recommendation signal (for the planner)

Primary: **(b) durable registry written synchronously at the bash spawn seam** (`bash.py:832` neighborhood) — bash-only writer, restart lane never writes it (structurally excluded, `upgrade_journal.py:2913-2915`). Secondary: **(a) env stamp** reusing the `proc_tools` Layer-3 pattern for on-host verification before kill — with the documented macOS `ps eww` limitation as an accepted fail-open. (c) supplies the kill primitive (`killpg` on the registered pgid).

---

## 3. BOOT RECONCILE PRECEDENTS (one-shot vs interval)

| Sweep | Where wired | Type |
|---|---|---|
| `recover_stale_job_locks` (C12 boot clear) | awaited in lifespan at `daemon/api.py:924-944` (non-fatal try/except; method `daemon/services/job_lock_manager.py:234`) | **ONE-SHOT at boot** |
| `JobLockSweepService` (F3) | constructed+`start()` at `daemon/api.py:1652-1676`; stopped at :3798-3805; class `daemon/services/job_lock_sweep.py:93` (interval default 90s :90; loop :222-241) | **INTERVAL (90s, always-on)** |
| `reconcile_terminal_watches` | ONE-SHOT boot call `await job_queue_service.reconcile_terminal_watches()` at `daemon/api.py:2481-2484` (method `daemon/services/job_queue_service.py:516`); later ALSO made a periodic backstop by `WatchReconcileSweepService` (`daemon/api.py:1679-1725`, first tick fires immediately :1700-1704) | **one-shot at boot** (+ later periodic twin) |
| `reconcile_drift_states` (job recovery) | periodic task `_periodic_drift_reconcile_loop` (`daemon/api.py:3915-3974`) created at boot `daemon/api.py:1498-1511` (`name="drift-reconciler"`); method `daemon/services/job_recovery_service.py:992` | **INTERVAL (300s default; boot reads knobs once :1470-1489)** |
| Upgrade-journal boot reconcile | ONE-SHOTs at lifespan: `reconcile_pending_op` (`daemon/api.py:2821-2828`), `close_stale_restart_arm` (:2837-2851), wake boot sweep (:2852-2859); then `UpgradeJournalSweepService` periodic ticker + executor reaper (default 90s, `daemon/services/upgrade_journal_sweep.py:96-99`; wired :2790-2830, stopped :3807-3821) | **one-shot at boot + interval twin** |
| `_ensure_postgres_columns` (PG schema evolution) | called in manager DB init when `is_postgres` (`daemon/manager.py:930-931`; defined :7391; idempotent `IF NOT EXISTS` rationale :918-929); maintenance boot sweep runs inside `manager.initialize()` (`daemon/api.py:910-914` → `daemon/services/maintenance_boot_sweep.py`) | **ONE-SHOT at boot (idempotent)** |

Hook points: lifespan startup in `daemon/api.py` (function `lifespan` at :521) and `InstanceManager.initialize()` (`daemon/manager.py:3608`). The repo convention the new design should follow: a **ONE-SHOT, awaited, non-fatal boot reconcile** in the lifespan (the `recover_stale_job_locks` shape, `api.py:933-944`), with no perpetual polling loop unless a steady-state backstop is separately justified.

---

## 4. DURABLE SUBSTRATE

### 4.1 How new PG tables register today

- **Manager-level `create_all`:** `SQLModel.metadata.create_all(self._engine)` in the manager DB init (`daemon/manager.py:909`); model modules are imported BEFORE it so they land on existing PG DBs (:885-907 pattern).
- **Lifespan-level `create_all` for late-imported models:** explicit `<Model>.metadata.create_all(manager.engine)` next to the JobWatcher precedent (`daemon/api.py:952`, `:965`, `:982-983`, `:994`, `:1009-1010`) — "covers engines whose manager-level create_all ran before this model module was imported" (:956-960).
- **Migrations are SQLite-only; PG = create_all + `_ensure_postgres_columns`:** the migration runner "skips non-SQLite engines" (`daemon/manager.py:918-922`; `daemon/api.py:971-974`); brand-new tables "need no `_ensure_postgres_columns` mirror" (`daemon/api.py:975-976, :989-990, :1002-1007`; `daemon/manager.py:815-817`). Also confirmed in the auto-restart plan ground-truth (C2): "PG schema comes from SQLModel.metadata.create_all() + EnsembleManager._ensure_postgres_columns; migration runner is a deliberate NO-OP on PostgreSQL" (`.agents/shared/planning/auto-restart-upgrade/plan-overview.md:35`).
- **PG DDL nuance:** python-side defaults produce NOT NULL WITHOUT a server DEFAULT — e.g. `ResourcePidMap.spawned_at` declares `sa_column=Column("spawned_at", Text, nullable=False)` with no server_default (`daemon/models/resource_monitor.py:298-302`). New columns/tables that need a default must set `server_default=` explicitly.
- **Partial-unique live-row precedent (dual-dialect):** `uq_resource_pid_map_pid_active` with `sqlite_where`/`postgresql_where` on `exited_at IS NULL` (`daemon/models/resource_monitor.py:258-276`).

### 4.2 File-based durable-state precedents (boot-read)

- Release journal `releases/state.json` with atomic temp+mv writes and torn-write rejection (`scripts/upgrade/lib.sh:13-14` header, `journal_*` family).
- Upgrade-journal `pending_op` + pending-actions nonce registry under the install dir — read/reconciled at boot (`daemon/api.py:2821-2828`; `daemon/services/upgrade_journal_sweep.py:13-16`).
- `data/upgrade.log` (executor log, `upgrade_journal.py:2244` `executor_log_path`), `data/launcher.log` (`lib.sh:4987`), launcher state file `.launcher-state` (crash-budget; `launcher.sh:57-59` budget knobs), `ensemble.json` (data-dir config; `daemon/api.py:567`).

### 4.3 Options for the spawned-PID registry

- **Option 1 — PG table (recommended shape):** a small table keyed like `ResourcePidMap` (pid, pgid, instance_id, kind='bash', command-sanitized, host, boot_id, created_at). Register the SQLModel before `create_all` (manager.py:885-907 pattern) or add a lifespan `create_all` (api.py:952 precedent); brand-new table → NO `_ensure_postgres_columns` mirror (api.py:975-976). Timestamps: TEXT ISO via `now_utc_iso()` (`daemon/services/timestamps.py:63-72`) following the `spawned_at` precedent (`resource_monitor.py:298-302`). (m1 revision correction: `now_utc_iso()` returns AWARE ISO with `+00:00` — it is the sanctioned TEXT-timestamp producer; the revised plan D3 uses the aware label and drops the PG-UTC-binding citation.)
- **Option 2 — data-dir JSONL/JSON state file** (atomic temp+mv like `journal_*`, `lib.sh:13-14`), written at spawn, read by the one-shot boot reconcile; simplest, no PG pool-order concerns at boot, mirrors the upgrade-journal file authority.
- **Stale-row discrimination (boot sweep):** every row carries the WRITING daemon's identity; rows whose `boot_id` ≠ this boot's id (or `created_at` < this boot's start marker) are previous-boot rows → candidates for kill (after verification). Options for the boot marker: (i) a UUID minted at manager init and persisted beside the registry; (ii) reuse the daemon start time the boot sweep already has in scope. Either way, kill only after positive attribution: pid alive (`os.kill(pid, 0)` liveness predicate precedent `daemon/services/upgrade_journal_sweep.py:27-32` — "pid-existence alone is NOT load-bearing"), pgid still matches the row, and (Linux) `/proc` starttime-jiffies or environ stamp matches (jiffies captured at spawn `bash.py:830`; PID-recycle contract `pid_registry.py:33-36`), (macOS) `ps eww` env check per `proc_tools.py:368-443` with its fail-open caveat. The two-signal liveness predicate in the upgrade reaper (pid + recent evidence) is the repo's stated answer to PID recycling.

---

## 5. DAEMON SHUTDOWN PATH

- **Entry:** `InstanceManager.shutdown(grace_period=10.0)` — `daemon/manager.py:15165` (re-entrancy guard :15186-15190). Called from the FastAPI lifespan teardown: `yield` at `daemon/api.py:3432` → shutdown sequence (:3434+) → `await manager.shutdown()` at `daemon/api.py:3858` (last step, after sweep stops and db_offload drain :3850-3855).
- **Existing bash cleanup call site:** Phase 2 of `manager.shutdown` — `daemon/manager.py:15229-15244`: `bash_killed = await get_bash_process_registry().cleanup_all()` at :15235, best-effort with WARNING on failure (:15240-15243). Phase 1 is the twin `BackgroundProcessManager.cleanup_all` at :15214-15227 (:15217). Both cite the same known limitations ("setsid orphans, crash-recovery leak", :15207-15213, :15229-15231 — mirroring `bash.py:477-501`).
- **Per-instance (terminate) site:** `daemon/services/instance_lifecycle.py:2751-2789` — proc `cleanup_instance` :2775, bash `cleanup_instance` :2789 (hoisted AFTER lifecycle mutation, CR-4/TD-5 comments).
- **Where an event-driven "kill tracked+stamped bash children on shutdown" hook lands:** immediately adjacent to Phase 2 — i.e. a Phase-3 block in `manager.shutdown` after :15244 (before the `steps` teardown list at :15246), or by extending `BashProcessRegistry.cleanup_all` itself. Constraint to respect: the restart executor is safe to leave alive at teardown ("C2 benign-detach" rationale, `daemon/api.py:3807-3812`) precisely BECAUSE it is unregistered; a durable-registry sweep must key strictly on registry rows (bash-only writers) so it can never reach the executor or the nohup launcher lineage.
- **No `atexit`/signal handlers found in `daemon/api.py`/`daemon/manager.py`** — the lifespan teardown is the graceful path; the launcher owns SIGTERM forwarding with `CHILD_STOP_WAIT_S=70` (`launcher.sh:60`).

---

## 6. PLATFORM NOTES (macOS + Linux)

- **No `/proc` on macOS:** starttime-jiffies reads are Linux-only and best-effort — `bash_start_time_jiffies` is `None` on macOS/Windows (`daemon/tools/bash.py:702-708`, `_read_proc_start_time` :300-330); the pid_registry lookup falls back to pgid when `/proc` is unreadable (`daemon/services/resource_monitor/pid_registry.py:33-36`).
- **No `setsid` BINARY dependency:** all detachment uses Python `start_new_session=True` (`bash.py:729-730`; `proc_tools.py:1179-1181`; `upgrade_journal.py:3100`) — `setsid(1)` is util-linux, "not POSIX", and the repo's promote-preflight work explicitly documented that constraint and its fallback (`.agents/shared/planning/promote-preflight-hang/phase2-plan.md:241-244`).
- **`os.killpg` semantics:** `BashProcessRegistry._kill_group` — SIGKILL the group on non-Windows, `taskkill /F /T /PID` fallback on win32 (`daemon/tools/bash.py:503-514`); `_kill_process` does SIGTERM→(bounded wait)→SIGKILL on the pgid (:582-631). A live-proof for group-kill semantics already exists (activation note: "2 agents × 15 parallel same-file appends… 0 lost" — killpg/append race fix, tests/unit/test_filesystem_write_race_fix.py exists in-tree).
- **`sys.platform` splits in process-handling code:** `bash.py:729` (spawn kwargs), `bash.py:506` (kill fallback), `proc_tools.py:1180` (spawn kwargs), `proc_tools.py:275-285` (ownership-verification dispatch: `linux` → procfs, `darwin`/`freebsd` → `ps eww`, else fail-open), `service_spawner.py:167-179` (`start_new_session=True ≡ C setsid(2)`).
- **psutil: NOT a dependency** — absent from `pyproject.toml` (grep: no match) and explicitly banned: "NO psutil. Zero new deps" (`daemon/services/resource_monitor/proc_readers.py:15`, rule restated :379).
- **Process-enumeration utilities that exist:** single-pid env dump backends (`proc_tools.py:288-365` Linux `/proc/<pid>/environ`; `:368-503` macOS `ps eww -p <pid>` with the "ordinary CLI children show EMPTY env" caveat :378-384; anchor corrected in revision pass), and the resource_monitor per-pid readers (`proc_readers.py`). There is NO full-process-table enumeration helper in-tree; a macOS boot sweep would need a fresh `ps -axo pid,ppid,pgid,etime,command` scan (no repo precedent to copy).
- **macOS-specific conclusion for the design:** the positive discriminator must be registry-driven (candidate b); the env stamp (candidate a) is verifiable on Linux, only sometimes on macOS.

---

## 7. TESTS (existing patterns to extend)

**Bash registry / cleanup lanes**
- `tests/test_manager_shutdown.py:146-346` — shutdown MUST call `BackgroundProcessManager.cleanup_all` (:154-171), exception isolation (:176-207), zero/positive-count logging (:214-270), `BashProcessRegistry.cleanup_all` awaited (:287-304) + exception isolation (:308-328) + count logging (:340+). Direct template for a Phase-3 durable-sweep shutdown test.
- `tests/tools/test_bash_registry.py` — `BashProcessRegistry` register/unregister/killpg unit lanes.
- `tests/tools/test_bash.py`, `tests/tools/test_bash_cancel.py` — bash tool spawn/timeout/cancel cleanup paths (the `bash.py` seams a stamp/registry-write would touch).
- `tests/tools/test_bash_secret_env.py` — the spawn-time env-assembly seam (`os.environ ⊕ overlay`) — where an env stamp would be asserted.
- `tests/integration/test_service_tool_kill_site_exemption.py` — THE protected-lane precedent: setsid-detached service children are NOT in `BashProcessRegistry` ("spawn does setsid + no registry" :122) so neither `killpg` (:176-178) nor registry sweeps (:262-300 K4/K5, :412-439 K8) reach them — the same structural-exemption shape the restart lane needs pinned.
- `tests/job_queue/test_job_feedback_observer.py:2492-2638` — mirror proc-tier tests for `BashProcessRegistry` at terminate time.

**Restart/upgrade executor + env isolation**
- `tests/unit/tools/test_agent_restart.py:188-193` — T0c pin: `ENSEMBLE_RESTART_LIVE`/`ENSEMBLE_UPGRADE_LIVE` NOT in `EXECUTOR_ENV_ALLOWLIST` (env-isolation oracle the new stamp must extend: assert `ENSEMBLE_BASH_CHILD`-family stamp absent from the allowlist → structurally cannot cross the restart boundary).
- `tests/unit/tools/test_upgrade_journal.py:34, :768-801` — `executor_env` allowlist purity + `reconcile_pending_op` lazy closure.
- `tests/unit/tools/test_upgrade_executor_systemd_service.py:11, :246-301, :397-414` — R3 `--setenv` threads the FULL `executor_env`; allowlist-membership scan; refusal→reconcile closure.
- `tests/unit/tools/test_promote_cgroup_survivorship_python.py:27, :290-346` — bus-discovery vars survive `executor_env`; `ENSEMBLE_UPGRADE_LIVE` stripped (pin cited at `upgrade_journal.py:2155`).
- `tests/unit/tools/test_post_restart_arm_notify_journal.py:702-726` — `reconcile_pending_op` must not touch pending wakes.
- `tests/mocks/upgrade_tools_live_safety_mock.py:791-885` — REAL `spawn_executor` with a fixture payload + fake executor completion "mirrors restart.sh §5 finalize EXACTLY" (:885) — harness for spawn-shape tests.
- `tests/test_launcher.sh` — launcher pure-logic seam pins (source-guard contract + ADR-012 journal sweep + `_pid_alive` twin-body pin; `launcher.sh:11, :40-43`).
- `tests/helpers/preflight_mutation_apply.py:443` — budget-breach semantics for restart.sh (recovery-class script must warn-and-continue).

**Env-stamp verification prior art**
- `tests/tools/test_proc_secret_env.py:12, :48, :140-232` — `_build_child_env` = `os.environ ⊕ tracking_id` byte-contract pins.
- `tests/tools/test_proc_tools.py:1308-1516` — tracking_id plumbing + `_verify_pid_ownership` patch-target tests (fail-open vs fail-CLOSED).

**Boot-sweep shape**
- `tests/unit/services/test_upgrade_executor_systemd_service.py` (above) and the boot-reconcile call sites covered by `tests/unit/tools/test_agent_restart.py:555-600` (`close_stale_restart_arm` boot-lane closure → `restart_abandoned`).
- E2E daemon spawn hygiene: `tests/e2e/test_mcp_tools.py:106`, `tests/e2e/test_mcp_tools_restore.py:176` — e2e harnesses spawn daemons with `preexec_fn=os.setsid` for clean process-group cleanup.

**Prior-art planning docs (design intent + protected-lane rationale)**
- `.agents/shared/planning/auto-restart-upgrade/plan-overview.md` — supervisor/ADR ground truth (incl. C2: PG create_all + `_ensure_postgres_columns` is the PG schema path; :35).
- `.agents/shared/planning/self-restart-upgrade-phase2/plan-overview.md:125` + `phase2-plan.md:24, :42-44` — daemonization rationale ("no tool survives process death"; NOT registered in BashProcessRegistry); `decisions.md:179, :280, :363` (executor seam rulings, env-allowlist R-SR09, Dispatch-B sandbox proof); `risk-register.md:228-232` (executor inherits env → allowlist mitigation).
- `.agents/shared/planning/auto-kill-bg-processes/plan-overview.md:121-122`, `phase2-plan.md:27, :419-423`, `phase3-plan.md:149`, `decisions.md:140-154` — PGID capture race, nohup-grandchild survivability, setsid-escape limitation, Windows taskkill fallback, `sys.platform != "win32"` guard consistency (proc_tools/bash).
- `.agents/shared/planning/upgrade-tool-lane-fix/plan-overview.md` and `.agents/shared/planning/upgrade-speed-overhaul/plan-overview.md` — upgrade-lane evolutions (adjacent context).
