# Rogue-Port Containment — Final Analysis (2026-11-11 bind-race restart-loop)

**Status:** **FINAL (revision 2)** — supersedes revisions 1, 1.1 and 1.2 of this file (interim drafts,
retained only in git-untracked history; their substance is fully carried forward or corrected below).
**Basis:** 5 dispatched analysts (3 mechanism/contract workers + 1 release-on-death worker + 1 sweep
re-frame) + a **2-council governor consensus** (`trade-off-analysis`, models `agentic` + `coding`) —
unanimous on every candidate verdict, on the lane split, and on the exit code; one timing divergence
on the supervisor-liveness item, adjudicated to *defer-with-trigger* (§5.4).
**Author:** Architect — analysis + recommendation only. No code, no commits, no live-host mutation.
**Source ref:** `origin/latest` = `efa8aaae28153b8e9c49b86abe19e92be5458ebe`.
**Live evidence:** `journalctl -u ensemble-main.service` (2026-11-11 06:00–12:00Z) · live `/proc` + `ss` ·
`/home/nea/agents-ensemble/.env` + `/home/nea/agents-ensemble/config.yaml` (read-only) ·
`/tmp/ens-e2e-evidence/` + `/tmp/ens-e2e-cOTp/` (the rogue lane's own artifacts) ·
`docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md`.

> The earlier orphan-genesis framing ("an orphaned fragment / a daemon mid-teardown held the port") is
> **FALSE** and was superseded by the host lane's receipt set. Its surviving findings are kept in
> **Appendix A** as a secondary note.

---

## 0. The one-paragraph answer

The 2026-11-11 outage was **not** a port-hold bug and **not** an orphan bug. It was a **port-resolution
precedence bug plus a missing ownership check**: the live install's `.env` declares `PORT=9797`, the
daemon exports it into its own environment (`launcher.sh load_env_file`), and the daemon then **hands
that environment down to every child it spawns** (`daemon/tools/service_spawner.py:173-181`,
`subprocess.Popen` with **no `env=`**). An e2e lane's hand-written launcher set only the *scoped* knob
`DAEMON_PORT=8099` — which is **inert**, because pydantic-settings resolves init-kwargs above env and
`config.yaml:113` always supplies `daemon.port` — and omitted `PORT`, so the rogue inherited
`PORT=9797` and bound **`0.0.0.0:9797`** in the ~40-second window after the live daemon released it.
It held the port for **2h38m** because the daemon's own supervisor could not stop its restart loop
(the launcher's burst budget is arithmetically unreachable at the observed per-cycle uptime), and
nothing in the system could tell a *foreign* instance from the *owner* of a port. The cure is four
lanes, **none of which touches `launcher.sh`, the unit template, or the live `.env`**.

---

## 1. THE PROVEN MECHANISM

### 1.1 The chain — every link receipt-cited

| # | Step | Receipt |
|---|---|---|
| **1** | The live install's `.env` declares the bind port: `PORT=9797`, `HOST=0.0.0.0`. | `/home/nea/agents-ensemble/.env:28,31`; demo `.env:31` = `PORT=7979` |
| **2** | `launcher.sh load_env_file` (`:201-246`) exports every `.env` key into the daemon's env. The systemd unit does **not** set PORT — only `Environment=MALLOC_ARENA_MAX=2` (`:121`) plus a drop-in's lone `Environment=` full-list reset. | `launcher.sh:201-246`; `scripts/systemd/ensemble-daemon.service:121`; `/etc/systemd/system/ensemble-main.service.d/10-zero-env-reset.conf` |
| **3** | ⇒ **The live daemon's own environment carries `PORT=9797`.** Measured: `/proc/3612324/environ` → `PORT=9797`, `HOST=0.0.0.0`. Every daemon-spawned child inherits it. | `/proc/3612324/environ` (read-only) |
| **4** | The rogue lane hand-wrote `/tmp/ens-e2e-evidence/launch.sh` (mtime **09:15** — the death window): `export DAEMON_PORT=8099`, `export DAEMON_HOST=0.0.0.0`, **no `PORT`**, **no `HOST`**; ends `cd /tmp/ens-e2e-cOTp/releases/v0.29.17 ; exec ./ensemble-prod`. | `/tmp/ens-e2e-evidence/launch.sh:9,10,19,20` |
| **5** | ⇒ The rogue inherited the live daemon's ambient `PORT=9797` / `HOST=0.0.0.0`; `config.yaml` interpolated them: `daemon.host: ${HOST:-0.0.0.0}` (`:112`), `daemon.port: ${PORT:-8088}` (`:113`) ⇒ **bound `0.0.0.0:9797`** — the live port, on all interfaces. | `config.yaml:110-113`; `daemon/config.py:8372` `yaml.safe_load` → `:8380` `substitute_env_vars` → `:8494-95` init-kwargs → `:8882 Config(**config_dict)` → `daemon/__main__.py:526-533` `uvicorn.run(host=…, port=…)` |

**The inheritance channel (the delivery vector) — and it is open on the live host right now:**
- `daemon/tools/service_spawner.py:173-181` — `subprocess.Popen(...)` with **no `env=`** ⇒ the child
  inherits the daemon's full environment (incl. `PORT`, `HOST`, `ENSEMBLE_CONFIG`,
  `LIMITS_METRICS_LISTENER_PORT`).
- `daemon/tools/bash.py:891-902` — the env overlay is applied **only when an overlay is passed**;
  otherwise the child inherits (`os.environ`), materialised as the "inherit-env" stamp at `:905`.
- **House precedent that the repo already knows this is dangerous**:
  `daemon/services/vscode_server_manager.py:81-94` — `_CODESERVER_STRIP_ENV` includes **`"PORT"`** with
  the comment *"PORT causes EADDRINUSE by overriding `--bind-addr`'s port"*. The daemon strips `PORT`
  for code-server **but not for the tools that spawn arbitrary work**.

### 1.2 The contrast that proves the fix shape — the lane's own two scripts

| | `boot_isolated.sh` (the GOOD fence) | `launch.sh` (the ROGUE) |
|---|---|---|
| main port | `export PORT=18790` (:41) | **absent** |
| scoped port | `export DAEMON_PORT=18790` (:42) | `export DAEMON_PORT=8099` (:9) — **inert** |
| bind address | `export HOST=127.0.0.1` (:43) | **absent** ⇒ inherited `HOST=0.0.0.0` |
| ambient scrub | **yes** — per-variable, `:15-25` (*"never `env -u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` — that re-arms the sweep"*) | **none** |
| outcome | bound `127.0.0.1:18790`; collided only on the unfenced 9798 | bound **`0.0.0.0:9797`** — the live port |

Corroboration from the rogue's own boot log: `daemon-boot.log:241` ⇒ the correctly-fenced boot bound
`127.0.0.1:18790`; `:180` ⇒ `[MetricsListener] DISABLED — bind failed on 127.0.0.1:9798` — **anomaly
07e8410c reproduced: a perfectly fenced daemon still collided on 9798.**

🔴 **The lane REGRESSED from a correct fence to a broken one** when it hand-wrote `launch.sh` at 09:15.
🔴 **`boot_isolated.sh` is NOT in the repo** at `origin/latest` — it was an *untracked scratch file*, so
the working fence has **no institutional artifact** to regress from. That is why option (e)-as-policy
is insufficient and option (e′)-as-code is required.

### 1.3 The timeline

| Time (Z) | Event |
|---|---|
| 09:15:06.124908 | Live daemon externally SIGTERMed. **Both ports released at T+0** — 9797 by uvicorn's first shutdown statement (`uvicorn/server.py:271-276`), 9798 by `MetricsListener.stop()` (`daemon/api.py:4297-4306`). |
| 09:16:12 | `[Spawn] spawn_instance auto-dispatch enqueue failed … Manager is shutting down` — the daemon was still tearing down. |
| 09:16:54.734862 | Live daemon exits (143) — **108.61 s after the ports were already free.** |
| 09:17:0x | The rogue (wrapper `3547660` / app `3547696`) captures 9797 **and** 9798. |
| 09:17:31 / 09:17:35 | The launcher's next child fails 9798 (`:180`) then 9797 (`[Errno 98] … ('0.0.0.0', 9797)`) ⇒ exit 1 ⇒ crash ⇒ relaunch. |
| 09:17 → 11:54:52 | **63 cycles.** Each boot fails both ports, tears down, exits 1. |
| 11:54:52.945707 | The launcher's **burst-abort**: `HALT: 6 crashes within 600s (budget 5) — exiting 1, staying down` ⇒ systemd `Restart=on-failure` ⇒ **KillMode=mixed cgroup SIGKILL killed the rogue** (`Killing process 3547660/3547696 (ensemble-prod)`) ⇒ ports freed. |
| 11:55:03 → 11:55:29.567861 | Fresh launcher ⇒ clean bind. |

**The launcher is strictly sequential** (`launcher.sh:3740-3742` `"$bin" & ; CHILD_PID=$! ; wait "$CHILD_PID"`)
and has **no port pre-check** (no `lsof`/`ss`/`curl`/`python3` — `launcher.sh:1862,:1899`). The previous
child was measured **dead 36.26 s min / 68.24 s median / 87.59 s mean / 191.25 s max before every bind
attempt** ⇒ **no daemon-vs-daemon race existed**; the only genuine race was the ~40 s handoff window,
and the rogue won it.

---

## 2. Q1 — THE PORT-RESOLUTION CONTRACT

### 2.1 Precedence — which knob wins

`load_config()` (`daemon/config.py:8339`): config path = `os.environ.get("ENSEMBLE_CONFIG")` or
`./config.yaml` (`:8359`) → `yaml.safe_load` (`:8372`) → **`substitute_env_vars(raw_config)` (`:8380`;
helper `:66`, expands `${VAR:-default}` IN the YAML)** → `config_dict["daemon"] = processed_config["daemon"]`
(`:8494-95`) → `Config(**config_dict)` (`:8882`).

| Rank | Source | Effect on the 9797 bind | Receipt |
|---|---|---|---|
| 1 | **`PORT` in the ambient environment** (interpolated into `config.yaml:113` *before* the model is built) | **AUTHORITATIVE** whenever set | `config.yaml:113`; `daemon/config.py:8380` |
| 2 | `daemon.port` literal in a custom `config.yaml` | wins only if `PORT` is unset | `config.yaml:113`; `daemon/config.py:8359` |
| 3 | **`DAEMON_PORT`** | **INERT whenever `config.yaml` has a `daemon:` key** | `daemon/config.py:564-570` (`env_prefix="DAEMON_"`), `:570` `port: int = Field(default=8079)` |
| 4 | `DaemonConfig.port` default **8079** | **UNREACHABLE** — the shipped `config.yaml` always has a `daemon:` block | `daemon/config.py:570`; `config.yaml:111-113` |

**Mechanism (proved by probe, not by assumption):** pydantic-settings resolves **init/kwargs > env >
defaults**. A field present in the init kwargs wins over the environment. Probe (worktree venv,
py 3.14.7 / pydantic 2.12.5 / pydantic-settings 2.13.1, env `DAEMON_PORT=8099`):

```
A DaemonConfig(port=9797).port            -> 9797   ← init kwarg beats env
B DaemonConfig().port                      -> 8099   ← no init kwarg ⇒ env wins
C DaemonConfig(host='0.0.0.0').port        -> 8099   ← port ABSENT from init ⇒ env wins
G Config(daemon={host}).daemon.port        -> 8099
J Config(limits={mx:50,mlp:9798}).mlp      -> 9798   ← yaml literal beats env
E Config(limits={mx:50}).limits.mlp        -> 19999  ← yaml omits the key ⇒ env wins
```

**This is already DOCUMENTED in-repo**, so it is a *known contract*, not an accident:
`config.yaml:224-229` — *"pydantic binds the init_kwarg instead of the env value — pydantic-settings
priority is args/init_kwargs > env > defaults"*; `launcher.sh:1422-1428` — *"`PORT` IS the source of
truth and `DAEMON_PORT` … only takes effect when the yaml carries no `daemon.port` key. (Verified
empirically at the fix cycle: `D(port=8088).port == 8088` with DAEMON_PORT=9999 set.)"*;
`Makefile:336-341` (ADR-014 "PORT is the SSOT").

**Verified against the LIVE install** (read-only): `/home/nea/agents-ensemble/config.yaml:109-111`
carries `host: ${HOST:-0.0.0.0}` / `port: ${PORT:-8088}` ⇒ **`DAEMON_PORT` is confirmed inert for the
live daemon**, and `PORT` is the live's effective knob.

### 2.2 The metrics port is on a different key, and is unfenced

| | Main port | Metrics port |
|---|---|---|
| Bind site | `daemon/__main__.py:529` `uvicorn.run(port=config.daemon.port)` | `daemon/api.py:2694-96` `port=int(getattr(config.limits,"metrics_listener_port",9798))` |
| Config key / class | `daemon.port` / `DaemonConfig` (`env_prefix="DAEMON_"`, `:564-570`) | **`config.limits.metrics_listener_port`** / `LimitsConfig` (`env_prefix="LIMITS_"`, `:593-596`; field `:795-798`, default 9798) |
| YAML key | `config.yaml:113` `port: ${PORT:-8088}` | live `config.yaml:187` `limits:` — **no `metrics_listener_port` key** ⇒ the **9798 default always applies** |
| Moved by `PORT`? | **yes** | **NO** |
| Moved by `LIMITS_METRICS_LISTENER_PORT`? | n/a | **YES** — *only because the YAML omits the key* (probe E) |
| Moved by the **DB** tier? | no | **YES — and it is applied at boot**: `daemon/api.py:749` `apply_db_settings_overrides(config, ensemble_config, …)` runs **1945 lines before the bind at `:2695`** and **mutates `config.limits` IN PLACE** (`settings_service.py:1268`; `config.py:718` *"the apply mutates IN PLACE"*; `manager.py:310,:761,:827`). The managed-key spec (`settings_service.py:524-544`) declares precedence **DB override > env > yaml > default** (`:536-542`). |

⇒ 🔴 **Three consequences.**
1. **Anomaly 07e8410c is structural**: `PORT` cannot reach 9798, so *any* sandbox/e2e daemon collides
   with the live metrics listener unless it sets `LIMITS_METRICS_LISTENER_PORT`.
2. **The env lever for 9798 is subordinate to the DB tier** — it wins only if no managed row exists.
   ⇒ a real 9798 fence must resolve **after** the boot-apply, or **derive** 9798 from the resolved main
   port (safe for live: 9797→9798 unchanged), so that no tier can move them apart.
3. **There is a second, DB-mediated stealing channel for 9798**, independent of `PORT`: a **same-DB**
   instance can write the managed row. (Not how the rogue took it — it used a disposable PG cluster —
   but a live-steal path for any lane that shares the live DB.)

### 2.3 Bind-surface census (complete)

**The daemon process has exactly TWO TCP binds.** Everything else is either OS-assigned, a stdio
*client*, or not a network socket at all.

| Surface | Bind | Fenced by `PORT`? |
|---|---|---|
| uvicorn main (`daemon/__main__.py:526`) | `0.0.0.0:9797` | **yes** |
| MetricsListener (`daemon/api.py:2694`) | `127.0.0.1:9798` | **no** |
| code-server (`vscode_server_manager.py:268`) | `127.0.0.1:<OS-assigned>` (`--bind-addr 127.0.0.1:0`) | n/a — `PORT` is stripped from its child env (`:85-94`) |
| MCP servers (`connection_manager.py:218,499`) | **no local bind** (stdio child, or a remote sse/streamable-http *client*) | n/a |
| live-views / KB MCP SSE | served on the main app (9797) | inherits main |
| plugin subsystem "ports" | **logical capability ports, not network** (`plugin_subsystem/port_registry.py`) | n/a |
| opendesign daemon | a separate systemd unit / process | n/a |

### 2.4 Fence levers that exist today

| Lever | Works today? | Receipt | Touches a live-visible file? |
|---|---|---|---|
| `PORT` | **yes** (authoritative) | `config.yaml:113` → `config.py:66-78` → `:8495` → `:8882` | no |
| `LIMITS_METRICS_LISTENER_PORT` | **yes** (yaml omits the key) — but **below the DB tier** | probe E; `config.py:795-798` | no |
| `ENSEMBLE_CONFIG` → a scratch `config.yaml` | **yes** — and it is the **only** way `DAEMON_PORT` becomes live | `config.py:8359`; probe `load_config(nodaemon)` → 8099 | no (scratch file) |
| `DAEMON_PORT` | **only** with a yaml lacking `daemon.port` | probe G/H | no |
| `HOST` | **yes** — but changes the live bind address | `config.yaml:112` | no |
| different CWD → different `./config.yaml` | **yes for a direct binary spawn**; **no** via the launcher (`launcher.sh:3887` `cd "$INSTALL_DIR"`) | `config.py:8359` | no |

---

## 3. Q2 — RELEASE-ON-DEATH: THE CONTRACT WAS NEVER VIOLATED

### 3.1 The physics, stated plainly

The kernel closes a process's file descriptors on **every** exit path — `do_exit()` → `exit_files()` →
`put_files_struct()` → `filp_close()` → `sock_release()` → `tcp_close()` → `inet_csk_listen_stop()` —
**unconditionally, with no userspace cooperation**: normal `exit()`, SIGTERM, SIGKILL, OOM-kill, SIGABRT
and fatal signals are identical. A **zombie holds no fds** (the fd table is destroyed before the task
becomes a zombie).

> 🔴 **A DEAD PROCESS CANNOT HOLD A LISTENING SOCKET.** Every "the dying daemon's wind-down held 9797"
> hypothesis is **killed by kernel semantics alone**. Measured: a full `/proc/*/fd` scan for the
> `0.0.0.0:9797` inode (8486543) and the `127.0.0.1:9798` inode (8486526) found **exactly one holder
> each** — pid `3612324` fd=30 and fd=24. Its parent (`3612318`, bootloader) and grandparent
> (`3612228`, launcher) hold **neither**. No zombies present.

**Child-inheritance is also impossible:** the LISTEN socket is created by asyncio's `loop.create_server`
(`uvicorn/server.py:169-182`) ⇒ **FD_CLOEXEC / non-inheritable** (measured `F_GETFD=1`,
`inheritable=False`); uvicorn's `sock.set_inheritable(True)` (`uvicorn/config.py:546`) sits only on the
`bind_socket()` path, unreachable with `uvicorn.run(reload=False, workers=1)`
(`daemon/__main__.py:530`); every daemon spawn uses `close_fds=True`
(`daemon/tools/service_spawner.py:180`, `daemon/tools/upgrade_journal.py:3447,:3537`); zero
`os.fork` / `multiprocessing` / `pass_fds` in `daemon/**`.

### 3.2 What actually happened

The live daemon released **both** ports at T+0 and then lived **108.61 s** longer. The MetricsListener
is an in-process daemon thread (stdlib `ThreadingHTTPServer`), stopped at `daemon/api.py:4297-4306`
*before* `manager.shutdown()` (`:4506`). The holder was a **live sibling**, indistinguishable from the
owner at kernel level. **The release-on-death contract worked exactly as designed; the defect was that
nothing prevented a second live instance from taking the port.**

### 3.3 The two adjacent defects that ARE real (neither is a port-hold)

**(i) The teardown budget is mis-modelled.** Measured teardown **108.61 s** vs `CHILD_STOP_WAIT_S=70`
(`launcher.sh:60-62`) and `TimeoutStopSec=90` (`scripts/systemd/ensemble-daemon.service:115`). The
unit's own comment (`:60-63`) asserts *"daemon graceful drain ~60 s + launcher margin 70 s → 90"* —
**false by measurement**. `daemon/config.py:571-585` names the launcher's SIGKILL as "the real hard
bound on total shutdown" — that contract is **self-inconsistent** with the measured 108.61 s.

Measured teardown profile: T+0 both ports free · T+1 `proc_tools cleanup_all` · T+31 `bash cleanup_all`
+ durable sweep #1 (316 procs) · **T+31→93 the 60 s durable bash sweep OVERRUNS its own deadline**
(`bash_spawn_reaper.py:132` → `WARNING … shutdown sweep exceeded its 60s aggregate deadline … remaining
rows left OPEN`) · T+93 `stop_sources` · T+94→107 worker pools (chat ×2, worker ×7) · T+107 event bus,
maintenance, **checkpointer closed**, MCP warm-up pool drained · T+108 `cleanup()` → DB engine disposed.

**Two competing SIGKILL sources — the launcher always wins:**
```
T0        systemd TERMs the MAIN process (launcher)      [KillMode=mixed: TERM to main only]
T0+0.1s   launcher trap forwards ONE SIGTERM to the daemon (launcher.sh:3683-3690)
T0+0.2s   daemon closes 9797/9798, begins teardown
T0+70s    launcher _reap_child_bounded → SIGKILL child    (CHILD_STOP_WAIT_S=70)
T0+90s    systemd TimeoutStopSec → SIGKILL cgroup        ← NEVER REACHED (launcher exited at ~70s)
```
**Skipped at a 70 s cut:** the tail of the 60 s durable sweep (rows left OPEN) **and every step from
T+93 on** — `stop_sources`, `cancel_active_requests`, `wait_inflight`, `shutdown_worker_pool` (both
pools), `shutdown_event_bus`, `shutdown_maintenance_service`, `shutdown_maintenance_api_service`,
`drain_snapshot_capture_tasks`, `dispose_db_pools`, `close_checkpointer`, `drain_mcp_pool`,
`shutdown_mcp_service`, `cleanup()` (DB engine dispose) — `daemon/manager.py:16653-16689`.
**Consequence = durability/consistency, NOT port-hold.** The bound should be ≥150 s, and the dominant
60 s term (explicitly non-fatal/retryable) should be shortened so the stop fits.

**(ii) The burst budget is structurally unreachable.** `BUDGET_MAX_CRASHES=5`, `BUDGET_WINDOW_S=600`,
`BUDGET_UPTIME_RESET_S=600` (`launcher.sh:57-59`); `budget_tick` (`:3624-3646`); crash branch
`:3836-3872` (`_sleep_interruptible "$backoff"` at `:3872`). The backoff ladder before the 6th crash is
10+20+40+80+160 = **310 s**, so abort requires `310 + 5U ≤ 600` ⇒ **U ≤ 58 s**. Observed U = 87–95 s ⇒
the 6th crash lands at **775 s** ⇒ the **aged-window** branch (not the uptime branch — U=93 < 600) fires
`reset` **every single time**: 13 consecutive ladders, each ending `action=reset`
(`09:16:55 #1 reset(49183s)` → `09:18:39 #2` → `09:20:32 #3` → `09:22:47 #4` → `09:25:41 #5` →
`09:29:57 #1 reset` (782 s) → … → `11:54:52 HALT: 6 crashes within 600s (budget 5) — exiting 1`).
It became reachable only when U fell to 39–43 s (310 + 5×41 = 515 ≤ 600) after the failed child's
post-fatal teardown shortened 64 s → ~16 s. **The defect is an internal inconsistency between three
constants**: a 600 s window, a 5-crash budget, and a mandatory 310 s backoff ladder.

---

## 4. Q3 — THE BOOT SWEEP, RE-FRAMED

### 4.1 Plain answer: the sweep WOULD have cured this class — the predicate is not the hole

Clause-by-clause against the rogue:

| Clause | Code | Rogue |
|---|---|---|
| 1 same cgroup | `:469-471` | **PASS** — the rogue inherited the live unit cgroup (`setsid` changes session/pgid, **not** cgroup) |
| 2 strictly older starttime | `:413`, `:472-473` | **PASS** — the rogue (09:17:0x) is older than any later boot |
| 3 not self / ancestor / **descendant** | `:457-461`; ppid-based walk `:421-453` | **PASS** — the rogue's chain (`rogue_app → rogue_wrapper → agent-bash → **dead** old daemon app`) does **not** pass through the sweeping daemon ⇒ **not a descendant** |
| 4 not kernel thread / not zombie | `:462-468` | **PASS** |

There is **no name/exe exclusion clause** (`:464-466` tests cmdline *emptiness* only; `:483` is logging
only) and the enumeration is a **full `/proc` scan** (`:423`), not a registry ⇒ no stale-snapshot hole.

⇒ **All four clauses pass ⇒ the rogue was a candidate ⇒ SIGTERM (`:534`) → SIGKILL at +5 s (`:577`) ⇒
uvicorn closes 9797 as the first statement of `Server.shutdown()` ⇒ port freed ⇒ the loop would have
lasted ONE cycle, not 63.**

**It never fired for exactly one reason: the sweep was DISARMED** — live `.env:115`
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` ⇒ `kill_switch_from_env` (`:214-221`) ⇒ OFF in both lanes ⇒
`sweep_stale_subprocesses` (`:383-390`) returns immediately ⇒ **zero `/proc` reads, zero signals**.

**Do NOT narrow the predicate.** Narrowing to "exe basename == ensemble-prod" would not change this
incident (the rogue *is* an `ensemble-prod` pair) and would destroy the class the sweep was built for —
the 4.2-day `context7-mcp` orphan — which is **already pinned**:
`tests/test_opt_o8_stale_subprocess_sweep.py:167-168` (pid 500, `ppid=1`, setsid'd, prior-incarnation
MCP-class orphan) with pins at `:194`, `:250`, `:272`, `:289`, `:325`, `:379/:407/:440`, `:464`, `:502`,
`:534`, `:672/:693/:711`. **No pin deliberately excludes a case covering this incident** — the orphaned
node case *is* the pinned case.

### 4.2 Why the live lane disarmed it (provenance)

| Evidence | Finding |
|---|---|
| `docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md:175-176` | *"Live inertness probe (read-only, poisoned signal path): scanned=183, candidates=0, actions=0"* ⇒ **the sweep WAS armed and running in the LIVE lane on 2026-10-18** |
| live `.env` snapshots | `.env.bak-2026-10-20T21:30:40Z` → knob **absent**; `.env.bak.20261026T211534Z` → **present (=0)** ⇒ added to the live lane **between 2026-10-20 and 2026-10-26** |
| repo `.env.example` | knob **absent** ⇒ not template-copied |
| **demo** `.env` | knob **absent** ⇒ not blanket-applied across installs |

⇒ **A LIVE-LANE-SPECIFIC event**, and it **predates** the 2026-10-31 smoke-hazard incident (which was a
*staged-smoke* lane misfire, mitigated by a lane-local harness pin —
`tests/unit/agents/test_aegis_security_family.py:1722,1782,2547,2851`). The reason is **not in-repo**;
the retained journal (383165 lines from 2026-09-30) contains **zero** `Boot sweep:` action lines.

### 4.3 Observability (the two halves differ)

| Channel | Visibility |
|---|---|
| **journal** | INFO **dropped** — `install_root_logging()` is imported at `daemon/api.py:72` (at `daemon.api` module-import time) and `daemon.api` is imported only inside `uvicorn.run("daemon.api:app")` (`daemon/__main__.py:526-527`), i.e. **after** the sweep at `:389-392` ⇒ no handlers at sweep time. **WARNING escapes via `logging.lastResort` → stderr → journald, PREFIX-LESS.** ⇒ journal silence is **STRONG** evidence that no action was taken, **WEAK** evidence about arming. |
| **file log** | **meaningless** — the `RotatingFileHandler` (`daemon/logging_setup.py:153,:182`) is installed later, after `:392`. |
| arming | answer from the **env**: `grep -n ENSEMBLE_BOOT_SWEEP /home/nea/agents-ensemble/.env`; `tr '\0' '\n' < /proc/<live-app-pid>/environ \| grep ENSEMBLE_BOOT_SWEEP`. |

Exact journal substrings (grep **messages** — the logger prefix is dropped):
`"Boot sweep: SIGTERM stale child pid="` (`:538`) · `"Boot sweep: SIGKILL stale child pid="` (`:581`) ·
`"Boot sweep: SKIP pid="` (`:528`) · `"Boot sweep: KILL phase skip pid="` (`:571`) ·
`"still present after SIGKILL"` (`:603`) · `"Boot stale-subprocess sweep complete: scanned="` (`:617`) ·
`"Boot stale-subprocess sweep: aborted (fail-open): "` (`:625`) · call-site
`"Boot stale-subprocess sweep: skipped (fail-open): "` (`daemon/__main__.py:396`).
**HIT** `complete: scanned=… candidates=N` ⇒ armed; `candidates>0` ⇒ it acted.

### 4.4 Verdict

**Leave the predicate alone.** The predicate is correct and covers this class. The two real defects are
**arming** and **observability** — and re-arming as-is is **not safe**, because the reason for the
live-lane disarm is unknown and not in-repo. If defense-in-depth is wanted later, both councilors named
the same shape: an **ownership-scoped reclaim** (TERM a live holder whose exe is under no install root),
**never** select-by-port — the rule `scripts/stop-ensemble.sh:11-23` already states, written after a
repo-side verification **killed the real prod on 9797**.

---

## 5. THE RECOMMENDED FIX SET (council consensus)

**Council:** 2 councilors (`agentic`, `coding`), skill `trade-off-analysis`, both read-only.
**Convergence:** very high — unanimous on every candidate verdict, on the lane split, and on the exit
code. One genuine divergence on (h)'s timing (§5.4), adjudicated.

### 5.1 LANE 1 — ship the in-flight bind-retry release **UNCHANGED and UNBUNDLED**

Branch `feature/bind-race-fix` (observed HEAD **`f3214eb7a`** — *"fix(bind): retry EADDRINUSE on the
listener instead of exiting 1"*; the lane is moving, so re-read HEAD before acting).

**Why unbundled is right:** pre-binding before the lifespan drops the per-cycle uptime `U` from the
measured 87–95 s to **~30–34 s**, which makes the launcher's burst abort **reachable**
(`310 + 5U ≤ 600` ⇒ `U ≤ 58s`) ⇒ the 63-cycle / 2h38m loop collapses to **~8-minute bursts**, with
**zero** launcher, unit, `.env` or exit-semantics coupling.

🔴 **A NEW LOAD-BEARING CONSTRAINT FOR THE IN-FLIGHT LANE (not stated in its commit message):**
`bind_retry_max_wait_seconds` currently defaults to **30.0** (`daemon/config.py:591-592`, branch) with
the description *"NOT sized to cover a wind-down: uvicorn closes the LISTEN socket FIRST"*. **It must
not be raised above ~45 s**, or the burst budget becomes unreachable again and the 2h38m loop can
return. `BIND_FAILURE_EXIT_CODE = 1` and the exit-75/78 preflight verdicts are explicitly untouched
(`daemon/services/bind_retry.py:72,135,242-244`) — keep them that way.

🔴 **Promote-gate check:** verify `install_root_logging()`'s import resolves in the **frozen bundle** on
the `run_app → main(run_preflight=False) → _serve_with_bind_retry` path (the pre-bind moves work ahead
of the lifespan, which is where logging is installed).

### 5.2 LANE 2 — the port-ownership contract: **(b)+(c)+(f) as ONE mechanism**, daemon-side

**Shape (all three are one mechanism, not three features):**
1. **Claim before bind**, scoped by **install dir + pid**, with `/proc/<pid>/stat` **starttime** liveness
   (pid-recycling defence; house precedent).
2. **Refuse ONLY on positive proof of a live foreign owner** ⇒ **exit 78** + a holder-identity marker
   line (`pid` / `exe` / `cwd` / `install` / `port`).
3. **Claim absent or stale ⇒ take and bind.** **Write failure ⇒ degrade, never refuse.**
   **Unknown ⇒ retry, never refuse.**
4. **Install-dir scoping** makes staged smokes (which resolve the live install root) *non-foreign* ⇒ they
   can never refuse the live.

**Why this is the mechanism that closes the hole:** it refuses the port **even against an explicit
`PORT=9797`**, which option (a) alone would not.

🔴 **MANDATORY COMPANION (verified):** the launcher's refuse branch has **no `_notify_once`** —
`launcher.sh:3814-3816` logs and `exit 78`, while the tempfail branch (`:3821`) and the burst-abort
(`:3848-3851`) both call `_notify_once`. ⇒ **an exit-78 refusal is a SILENT-DOWN.** A daemon-side
**operator alert must ship in the same release as (f)** — without it, 78 is *quieter* than the crash loop
it replaces. This is not optional.

### 5.3 LANE 3 — (e′) the spawn-boundary env scrub, **by name, in code**

Strip `PORT` / `DAEMON_PORT` / `HOST` / `LIMITS_METRICS_LISTENER_PORT` from the environment the daemon
hands to children, at `daemon/tools/bash.py:891-915` and `daemon/tools/service_spawner.py:173-181`.

- **House precedent:** code-server already strips `PORT` for exactly this reason
  (`vscode_server_manager.py:81-94`: *"PORT causes EADDRINUSE"*).
- **Preserve deliberate PORT setters** (`daemon/manager.py:6411`; the promote-executor allowlist).
- 🔴 **The inheritance channel is open on the live host right now** (`/proc/3612324/environ`:
  `PORT=9797`) — the incident's delivery vector is live today.
- **(d) rides here**: fence the MetricsListener's 9798 with an **env-wins validator** (house precedent
  `MemoirConfig._env_tier_wins`, `daemon/config.py:4709+`) — **never** a YAML literal, which would kill
  the only working `LIMITS_METRICS_LISTENER_PORT` lever (probe J). Preferred alternative: **derive** 9798
  from the resolved main port (safe for live: 9797→9798 unchanged) so no tier can split them.

**Why (e′) is required and not merely advisable:** (e)-as-policy already failed once (measured
recurrence: 1), and the fence that *worked* (`boot_isolated.sh`) was an **untracked scratch file** — the
policy has no institutional artifact to regress from. (e′) moves the fence from the script into the
spawn boundary, where it cannot be forgotten.

### 5.4 LANE 4 — supervisor liveness: **DEFER, with an explicit trigger** (the one non-unanimous point)

*coding*: adopt the sliding-window burst fix now as insurance. *agentic*: defer — measure the new `U`
at the Lane-1 boot smoke; if `U ≤ 58 s` the fix is redundant.

**Adjudicated: DEFER with an explicit trigger.** Promote the **sliding-window** variant
(re-anchor `window_start = now` on every counted crash ⇒ "6 crashes with no ≥600 s quiet gap"; the
`uptime ≥600` exemption still covers a healthy daemon) to its own staged lane **iff** the measured
post-Lane-1 `U > 58 s`, or a loop recurs. Rationale: coding's own adoption argument *is* that trigger
condition; deferring avoids dragging the ceremony-critical `launcher.sh` + `manifest.launcher_sha256` +
the twin pins into this cycle **without measured need**. *If you prefer unconditional insurance,
coding's Lane-4-now is defensible* — the sliding-window variant breaks no machine contract (the
watchdog reads the persisted marker, `scripts/watchdog-watcher.sh:446-464`).

**Rejected in this lane:** the `-gt`→`-ge` predicate variant (breaks `tests/test_launcher.sh:215-216`
plus `scripts/watchdog-watcher.sh:434` `BUDGET_MAX_CRASHES=5  # MUST match launcher.sh` and its pin
`tests/test_watchdog_watcher.sh:26-30`; and `watchdog-watcher.sh:453-455` abort detection goes blind);
and the **teardown-budget half** (unit + watchdog twin breakage) — the ≥150 s reconciliation is a
separate, later, durability lane.

### 5.5 The exit code: **78, reused, with a marker line**

`classify_exit` (`launcher.sh:3555-3562`): `0→clean | 75→tempfail | 78→refuse | *→crash`; the unit has
`RestartPreventExitStatus=78` (`:110`) and `SuccessExitStatus=143` (`:111`).

- **75 is wrong** — it is **budget-exempt** (`launcher.sh:3821` `_notify_once "tempfail-75"`), so it
  produces an unbounded slow retry loop — the same class, less visible.
- **A new code is wrong now** — it lands in `*→crash` and loops unless `launcher.sh` learns it (sha-pinned,
  ceremony-critical) **and** the unit lists it — and whether a promote re-mints the live unit is
  **unverified**. Never piecemeal; a dedicated code (~73/79) is a long-term option only as a paired
  launcher+unit lane.
- **78 is exact**: *"restarting cannot fix this."* Accepted residual: a port-squat 78 during a promote
  window reads as a "clean pre-mutation exit-78" — mitigated by the marker line plus the 30 s retry
  covering normal promote hand-over.

### 5.6 Unanimous rejects

| Rejected | Why |
|---|---|
| **(a) make `DAEMON_PORT` authoritative** | The YAML-literal variant binds the live to **8088** (outage). The `DAEMON_PORT`-wins variant breaks ADR-014's "PORT is the SSOT" contract (`Makefile:336-341`, `launcher.sh:1422-1428`) **and silently inerts the launcher's serving-green probe** — `_js_sweep_probe_port` targets `PORT` (`launcher.sh:1439-1458`), so the probe and the daemon would disagree. |
| **`SO_REUSEPORT` on 9797** | The kernel requires **all** listeners on the addr:port to set it; the live does not and the rogue did not ⇒ EADDRINUSE persists ⇒ it would **not** have helped — and if both did set it, the squat becomes an invisible **split-brain** (two live daemons silently sharing 9797), converting a loud failure into a quiet one. **Must not be added.** |
| **(e) as policy** | It already failed once; the working fence was untracked scratch. Replace with (e′)-as-code. |
| **(g) re-arm the sweep as-is** | The disarm reason is live-lane-specific and unknown; the 2026-10-31 precedent is live; the sweep is structurally unobservable. The cure is (b)/(c)/(f) + (e′). |
| **(h) teardown-budget half; the `-ge` variant** | Unit + watchdog twin breakage; `watchdog-watcher.sh:453-455` abort detection goes blind. |

---

## 6. Q4 — THE LANE SPLIT

| Lane | Contents | Why this lane | Live blast radius |
|---|---|---|---|
| **1 — IN FLIGHT, ship as-is** | daemon-side bounded bind-retry (pre-bind + backoff, TOCTOU-free, `BIND_FAILURE_EXIT_CODE=1`, 75/78 untouched) | It is the **symptom** control and it is already correct; pre-binding drops `U` below the burst-abort threshold, which ends the 2h38m loop **for free** | binary swap only. **Constraint: keep `bind_retry_max_wait_seconds` ≤ ~45 s.** |
| **2 — NEW: port-ownership contract** | (b)+(c)+(f) as one mechanism: claim before bind (install dir + pid + starttime liveness); refuse only on positive proof of a live foreign owner ⇒ **exit 78 + marker line**; degrade-never-refuse; unknown⇒retry. **Plus the mandatory operator alert** (the launcher's refuse branch has no `_notify_once`) | It is the only control robust to a lane that sets `PORT` explicitly, and it is the only one that yields **attribution** ("who held 9797 at 09:15Z") | binary swap only. The **single** path that could stop the live binding 9797 is the refuse verdict — contained by positive-proof-only + install-scoping + degrade-never-refuse. |
| **3 — NEW: spawn-boundary env scrub** | (e′): strip `PORT`/`DAEMON_PORT`/`HOST`/`LIMITS_METRICS_LISTENER_PORT` by name at the two spawn seams; preserve deliberate setters. **(d) rides here**: 9798 env-wins validator (or derive 9798 from the resolved main port) | It closes the **delivery channel**, which is open on the live host right now; and it is where (d) belongs (a different config class ⇒ no 9797 effect) | binary swap only; no 9797 effect |
| **4 — DEFERRED (trigger-gated)** | sliding-window burst fix — **iff** measured post-Lane-1 `U > 58 s` or a loop recurs | Touches `launcher.sh` ⇒ manifest `launcher_sha256` + twin pins ⇒ ceremony-visible ⇒ own staged lane, and only with measured need | `launcher.sh` (promote-only) |
| **5 — LATER (durability, not this incident)** | reconcile the teardown budget with the measured 108.61 s (≥150 s) and shorten the dominant 60 s non-fatal durable sweep; the unit+launcher `WAIT_S`/`CHILD_STOP_WAIT_S` chain | A **durability/consistency** defect (DB pool disposal, checkpointer close, worker pools, bash-sweep rows left OPEN) — not a port-hold defect | `launcher.sh` + unit template |
| **POLICY** | lanes must never boot a live-shaped daemon; the fence must live in the **spawn boundary**, not in a per-run script | (e′) makes it non-load-bearing | none |

**Binding principle:** the recommended set touches **`launcher.sh` = ZERO times, the unit template = ZERO
times, the live `.env` = ZERO times** — all containment lands in the daemon binary. The only
ceremony-visible item (Lane 4) is deferred behind a measurement.

---

## 7. ADDITIONAL HARDENING (cheap, low-risk, independently valuable)

1. 🔴 **Make the boot sweep observable** — emit its summary at WARNING (or install logging before
   `daemon/__main__.py:392`) so the mechanism is not a black box. Its whole action surface is currently
   invisible in both the journal and the file log. *(files: `daemon/services/boot_subprocess_sweep.py:383-390`; `daemon/api.py:72`.)*
2. 🔴 **Settle the 10-2x live-lane sweep disarm** — recover the reason from the host (journal/history of
   2026-10-20..10-26); until then the sweep stays off. *(live `.env:115`.)*
3. 🟡 **Give the metrics port a fence that no tier can split** — env-wins validator or derive 9798 from
   the resolved main port. Also **verify on the live DB whether a managed row for
   `limits.metrics_listener_port` exists** (read-only `SELECT`): if it does, the env lever is already
   subordinate to the DB tier. *(files: `daemon/config.py:795-798`, `daemon/api.py:2694`, `:749`.)*
4. 🟡 **Reconcile the teardown budget with reality** — the 60 s→70 s→90 s chain is built on a
   mis-modelling (`graceful_shutdown_timeout_seconds` bounds only the *task drain*; the lifespan
   teardown is unbounded). *(files: `launcher.sh:60`, `scripts/stop-ensemble.sh:53-59,110-113`,
   `scripts/systemd/ensemble-daemon.service:115`.)*
5. 🟡 **Shorten the dominant 60 s durable sweep** so the stop fits a sane bound (it is explicitly
   non-fatal/retryable, yet it overran its own deadline on 2026-11-11).
   *(file: `daemon/services/bash_spawn_reaper.py:132`.)*
6. 🟢 **Ship a scratch `config.yaml` whose port is a literal** — otherwise an ambient `PORT` still wins
   through the interpolation and `ENSEMBLE_CONFIG` is not a real fence. *(file: `config.yaml:113`.)*

---

## 8. DECLARED HOLES AND RESIDUAL UNCERTAINTY

| # | Hole | Survival verdict |
|---|---|---|
| H1 | A rogue **in the same install dir** executing the live's own binary is "non-foreign" to the claim | Survives — needs per-boot identity in the claim, or the future ownership-scoped reclaim (§4.4) |
| H2 | The post-Lane-1 `U` is **inferred (~30–34 s), not measured** | Survives — the §5.4 trigger exists for exactly this; measure it at the Lane-1 boot smoke |
| H3 | The DB-managed metrics-port tier's live state is **unverified** | Survives — (d) is env-wins either way, so not load-bearing; but verify the row before relying on the env lever |
| H4 | A genuine ambient-`$PORT` consumer **outside** this repo would need a distinct alias var | Survives — and the desired direction is *loud failure*: after (e′) such a read fails loudly (corroborated by `.agents/tester/RESULTS/2026-11-02…:204`) |
| H5 | The exact kernel-source citation of `exit_files()`; the rogue's precise 9797 capture instant (bounded only by "≤09:17:31 for 9798, ≤09:17:35 for 9797"); the sender of the 09:15:06 SIGTERM (exit 143 with **no** launcher "received SIGTERM" line ⇒ external, not traced) | Non-load-bearing — none of these changes a recommendation |
| H6 | The live unit file is unreadable (Permission denied) ⇒ its contents were taken from `systemctl show` + the in-repo template | Non-load-bearing |
| H7 | The live `config.yaml` is not byte-identical to `origin/latest`'s (sha `8351444c…` vs `25f3d595…`; it is the v0.29.15 release copy) | Closed for the port keys (`daemon.port` present ⇒ `DAEMON_PORT` inert; `limits.metrics_listener_port` absent ⇒ the env lever works). A hand-edited live `config.yaml` is not excluded — but a literal `port:` there would already have made `PORT` inert, and it is not: the live binds 9797 with `PORT=9797` in `/proc/3612324/environ`. |

---

## 9. DECISIONS PENDING FOR THE LEADER

1. **Lane 4 (the burst-window fix) — defer or adopt now?** Recommendation: **defer with the trigger**
   (§5.4). If you prefer unconditional insurance, the sliding-window variant breaks no machine contract.
2. **The 9798 fence shape** — env-wins validator vs derive-from-main-port? Recommendation: **derive
   (or env-wins)**, never a YAML literal, and verify the live DB row first (H3).
3. **The refusal exit code** — reuse **78** (recommended, §5.5) or commission a dedicated code as a
   paired launcher+unit lane? Recommendation: **reuse 78** now.
4. **The operator alert for the refusal path** — it must ship in the same release as (f) (§5.2). Which
   channel? (The launcher has no `_notify_once` on its refuse branch; a daemon-side alert is required.)
5. **The 10-2x sweep-disarm investigation** — is a host-level lookup in scope, or does the sweep simply
   stay off? (H2/§7.2.)
6. **Verify the frozen-bundle `install_root_logging()` import** on the new bind-retry path before
   promoting Lane 1 (§5.1).

---

### Appendix A — superseded orphan-genesis findings (secondary note)

- **FALSE / superseded:** "an orphaned fragment or a daemon mid-teardown held the port". The holder was a
  **live** rogue instance; a dead process cannot hold a socket; the launcher is strictly sequential, so
  no daemon-vs-daemon race existed.
- **Still true and worth keeping:** (i) the boot sweep was globally disarmed in the live lane for
  ≥16 days (`.env:115`); (ii) the sweep is structurally unobservable; (iii) the teardown budget is
  mis-modelled (108.61 s real vs 70/90 s budgeted); (iv) the burst budget was unreachable for 2h38m.
- **True but irrelevant to this class:** the launcher's supervision depth (1 link vs a 3-level PyInstaller
  tree) and fd inheritance (already answered: non-inheritable, `close_fds=True` everywhere, zero
  fork/multiprocessing).
- **Corrected from rev 1:** the loop began at **09:17:31Z** (not 10:30:53Z) and ran **63** cycles (not ~35).

### Appendix B — receipt index

`config.yaml:110-113,224-229` · `daemon/config.py:66,571-585,564-570,593-596,795-798,8339,8359,8372,8380,8494-95,8512-13,8882` ·
`daemon/api.py:70,72,684,747-751,749,2694-96,4297-4306` · `daemon/__main__.py:389-392,402,506,526-533` ·
`daemon/tools/bash.py:144-167,253-274,891-915` · `daemon/tools/service_spawner.py:173-181` ·
`daemon/services/boot_subprocess_sweep.py:28-48,117,140-180,183-225,214-221,377-390,399-412,421-453,457-468,469-473,528-538,571-603,617-625,423,464-466,483` ·
`daemon/services/metrics_listener.py:71,265,339,500-527` · `daemon/services/vscode_server_manager.py:81-94,268` ·
`daemon/services/settings_service.py:524-544,1268` · `daemon/services/bash_spawn_reaper.py:132` ·
`daemon/manager.py:310,761,827,6411,16514,16653-16689` · `daemon/services/plugin_subsystem` (logical ports) ·
`launcher.sh:52-62,1422-1428,1439-1458,1862,1899,201-246,3555-3562,3683-3709,3740-3742,3760,3814-3816,3821,3848-3851,3881-3888` ·
`scripts/stop-ensemble.sh:5-31,44-59,110-113,434` · `scripts/systemd/ensemble-daemon.service:51-63,103-121` ·
`scripts/upgrade/stage.sh:424,438-439,595,599,611,790` · `scripts/upgrade/lib.sh:4059-4069,4128` ·
`scripts/upgrade/promote.sh:464,476` · `scripts/watchdog-watcher.sh:434,446-464,453-455` ·
`tests/test_release_journal.sh:433-437` · `tests/test_launcher.sh:193-233,215-216,1320-1331,2830-2844` ·
`tests/test_opt_o8_stale_subprocess_sweep.py:167-168,194,250,272,289,325,464,502,534` ·
`tests/test_watchdog_watcher.sh:26-30` · `tests/unit/agents/test_aegis_security_family.py:1722,1782,2547,2851` ·
`uvicorn/server.py:169-182,271-276,286-297,300-301` · `uvicorn/config.py:533-547` ·
`ensemble.spec:132-153` · `run_app.py:9,17-31,70-71` · `docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md:175-176` ·
live `.env:28,31,115` · live `config.yaml:109-111,187` · demo `.env:31` · `/proc/3612324/environ` ·
`/tmp/ens-e2e-evidence/launch.sh:9,10,19,20` · `/tmp/ens-e2e-evidence/boot_isolated.sh:15-25,41-43` ·
`/tmp/ens-e2e-evidence/daemon-boot.log:180,241` · branch `daemon/config.py:591-592` ·
`daemon/services/bind_retry.py:72,135,242-244`.

### Appendix C — corrections log (this revision supersedes all earlier ones)

| When | Correction |
|---|---|
| rev 1 → 1.1 | §4's "the sweep has no bearing" was **WRONG** (my error, not the sweep's): the rogue's ppid chain is broken, so it is **not** a descendant ⇒ clause 3 passes ⇒ **the sweep would have cured this class in one cycle**. The predicate is correct — do not narrow it. |
| rev 1 → 1.1 | The sweep's disarm provenance: armed and running **live** on 2026-10-18; disarmed **live-lane-only** between 10-20 and 10-26; reason **not in-repo** ⇒ re-arming as-is is unsafe. |
| rev 1 → 1.1 | Observability split: journal ⇒ **INFO dropped / WARNING prefix-less** (strong for "no action", weak for "arming"); file log ⇒ **meaningless**. |
| rev 1 → 1.1 | Option (a) has a coupling revision 1 missed: `_js_sweep_probe_port` targets `PORT` (`launcher.sh:1439-1458`) ⇒ `DAEMON_PORT`-wins silently inerts the serving-green arm. |
| rev 1.1 → 1.2 | The DB-managed tier for `limits.metrics_listener_port` **IS** consulted (`api.py:749` boot-apply, in place, before the bind) ⇒ a second DB-mediated 9798 stealing channel, and the env lever is subordinate to it. |
| rev 1.2 → 2 | `DAEMON_PORT` inertness **verified against the live install's `config.yaml`** (not inferred from `Makefile:342`); the live `limits:` block omits `metrics_listener_port` ⇒ the env lever is live-working today. |
| rev 2 | Council consensus replaces my provisional ranking: **(a) is rejected outright**; **(e) is replaced by (e′)-as-code**; **(f) is promoted into Lane 2**; **exit code 78 reused**; **Lane 4 deferred with a measurement trigger**. The recommended set touches **`launcher.sh` = 0, unit template = 0, live `.env` = 0**. |
| rev 2 | Branch HEAD observed as **`f3214eb7a`** (the lane is moving; the council's `7bca374a6` and my earlier `0c1e954ff` are both stale). Re-read HEAD before acting. |
