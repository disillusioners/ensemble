# P5 LEG B — Staged-Boot Manual-Trigger Proof — Central Maintenance System (maintsys-p5b)

- **Date:** 2026-11-22 (20:33Z–21:30Z)
- **Lane:** `feature/central-maintenance-system` @ worktree `/home/nea/ensemble-src-wt-maintsys` @ `edf698031`
- **Commission deliverable:** *"document and prove the exact trigger path in staging, with its receipt shape"*
- **Binary under test:** frozen `v0.31.34` — `/home/nea/agents-ensemble/releases/v0.31.34/ensemble-prod` (live-install dir, NOT the worktree source)
- **Verdict:** ✅ **PASS — the manual trigger path is proven end-to-end in a staged boot, and the receipt shape is captured verbatim.**
- **Sibling leg:** P5A (`2026-11-22-maintsys-p5a.md`) — the static/AST matrix. This leg adds the LIVE proof Leg A could not make: a real boot, a real fire, real lane children, real artifacts.

---

## 0. Scope, fences and the one deviation that mattered

### 0.1 LIVESAFETY fences actually applied

| Fence | Applied | Evidence |
|---|---|---|
| Frozen binary booted with an EXPLICIT, PER-VARIABLE scrubbed env | ✅ | 17 ambient `ENSEMBLE_*` names scrubbed one-by-one (§1.2) |
| NEVER sourced `/home/nea/agents-ensemble/.env` (the LIVE env) | ✅ | only the **worktree** fence `.env` was sourced |
| `env -u POSTGRES_*` never used as a glob idiom | ✅ | per-variable `-u` only; the ambient `POSTGRES_*` were in fact **unset**, so the dev fence values were the only ones present |
| `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **PINNED EXPLICITLY** | ✅ | set (not unset) — the 2026-10-31 frozen-sweep kill incident is structurally avoided |
| Port 8079 free-check before boot | ✅ | `ss -ltn` — 8079 free; live daemon left alone on its own port |
| ZERO interaction with the live daemon | ✅ | live daemon PID 268357 still running, uptime 4h21m, untouched |
| NO systemd commands | ✅ | none issued |
| NO writes to `state.json` / `current` | ✅ | §9 — `current` still `v0.31.31`; byte delta proven live-origin |
| Kill ONLY the exact PID started | ✅ | `/proc/806181/cmdline` verified before SIGTERM |
| Cut worktree (`…-wt-maintsys-cut`) untouched | ✅ | never referenced |

**Additional fences this leg added (beyond the dispatch brief):**

- `cwd` = scratch dir under `/tmp` — the frozen daemon resolves most runtime paths **cwd-relative** (`./data`), so the live install's `data/` was never written.
- `DAEMON_LOG_DIR` = scratch — the default is `./data/logs` (cwd-relative), which would have **clobbered the live daemon's `ensemble.log`**.
- `ENSEMBLE_DATA_DIR` = scratch — keeps the live install's `data/` clean.
- `ENSEMBLE_CONFIG` = the **frozen release's own** `config.yaml` (the release dir has no `docs/` and its own config; the live install's `config.yaml` is v0.31.x-live-shaped).
- The **artifact root was deliberately NOT fenced**: the design resolves it to the PROMOTE-STABLE install root, and that IS the AC8 requirement.

### 0.2 The one deviation, stated plainly

**The staged release tree has NO `docs/` directory.** The runbook was therefore read from the **branch worktree copy** (`/home/nea/ensemble-src-wt-maintsys/docs/runbooks/central-maintenance-manual-trigger.md`, 85 lines, mtime 2026-11-22 20:14) — the fallback the dispatch brief sanctions. The frozen release ships `agents/`, `daemon/`, `config.yaml`, `frontend/`, `plugins/`, `guidelines/`, `scripts/`, `manifest.json`, `launcher.sh` — but not `docs/`. **Consequence for the release lane: the operator-facing runbook is NOT in the staged release artifact and would not ship with it.** (Finding F1 below.)

---

## 1. Pre-picture + boot

### 1.1 PRE-PICTURE (file evidence only — no livez query)

```
$ cat /home/nea/agents-ensemble/releases/state.json | head -c 200
{"current":"v0.31.31","previous":"v0.31.27","in_flight":null,...
$ sha256sum …/releases/state.json
b2792e90dbfb13eeb177c2215210894a7a7a31c39722f50c293d467ac6a17a48
```

`current` = **v0.31.31** (as expected — v0.31.34 is STAGED, not promoted).

**/tmp picture (PRE):**

| Metric | Value |
|---|---|
| `ensemble-hbk-*` files (maxdepth 1) | **13 459** |
| `ensemble-hbk-*` bytes | **51 310** |
| `ensemble-hbk-*` POPULATED (>0 B) | **308** |
| `/tmp/gate-hbk-base*` | `357 808 776` B, **19 194** inodes (1 dir) |
| `/tmp` total | **52 220 860 451** B, **2 151 686** inodes |

### 1.2 The scrubbed env (proved before boot)

Ambient `ENSEMBLE_*` names (name-only match) — 19 present, of which 2 are explicitly set, **17 scrubbed**:

```
ENSEMBLE_AUTH_ALLOWED_ORIGINS  ENSEMBLE_AUTH_CLIENT_ID  ENSEMBLE_AUTH_CLIENT_SECRET
ENSEMBLE_AUTH_DISCOVERY_URL    ENSEMBLE_AUTH_ENABLED    ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
ENSEMBLE_AUTH_ISSUER           ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART
ENSEMBLE_BASH_TRACKING_ID      ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES   ← PINNED =0 (kept)
ENSEMBLE_JOB_SYSTEM_JOB_RETRY_SCHEDULER_ENABLED
ENSEMBLE_LAUNCHER_STATE_FILE   ENSEMBLE_MISSION_RECORDS
ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS  ENSEMBLE_PROMPT_ASSENT_ENABLED
ENSEMBLE_SELF_ENV              ← SET dev (kept)
ENSEMBLE_SKILL_CAPTURE_ENABLED ENSEMBLE_SKILL_EVOLUTION_ENABLED
ENSEMBLE_UPGRADE_SCRIPTS_DIR
```

Plus `MEMORY_PRESSURE_WATCH` (ambient value = the LIVE systemd cgroup path), `DATABASE_URL`, `DATABASE_URL_POSTGRES`.

Explicit sets: `POSTGRES_DB=ensemble_dev`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5432`,
`POSTGRES_USER=ensemble_dev_app`, `POSTGRES_PASSWORD=<dev fence value — sha256 prefix `654437afea15caf7`,
never the live .env>`, `DAEMON_PORT=8079`, `PORT=8079`, `ENSEMBLE_SELF_ENV=dev`,
**`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`**.

Child-env probe (post-scrub): only **3** `ENSEMBLE_*` vars survive — `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`,
`ENSEMBLE_CONFIG` (my fence), `ENSEMBLE_SELF_ENV=dev`. `INSTALL_DIR` (the frozen binary's own install root) and
the `OPENAI_*` proxy vars (ambient, needed for the agents to actually run) are inherited.

### 1.3 Boot attempt 1 — a PRECISE refusal, not a blind failure

```
DB RESOLUTION REFUSED (rogue-containment): tier=ambient (ensemble.json absent) with ambient POSTGRES_* present
(POSTGRES_HOST,POSTGRES_PORT,POSTGRES_DB,POSTGRES_USER,POSTGRES_PASSWORD) and ENSEMBLE_DB_AMBIENT_ALLOW != 1.
Refusing before any TCP connection. Set ENSEMBLE_DB_AMBIENT_ALLOW=1 for a sanctioned first boot
(the persisted ensemble.json makes later boots tier 1).
```

The frozen binary exited **before any TCP connection** (exit-78 track) — the dev DB was never contacted, so
the refusal left zero side effects. Root cause: `daemon/ensemble_config.py::assert_db_resolution_sanctioned`
fires iff `ensemble.json` is absent (tier 2) **and** ambient `POSTGRES_*` are present **and**
`ENSEMBLE_DB_AMBIENT_ALLOW != 1`.

**Remedy chosen — the stricter of the two sanctioned escape hatches:** persist a **tier-1** `ensemble.json`
in the scratch `ENSEMBLE_DATA_DIR` (no `ENSEMBLE_DB_AMBIENT_ALLOW` needed). Written mode **600**, and it
carries **no password** — `EnsembleConfig.get_postgres_url` is **env-wins for every field**
(host/port/db/user/password), so the credential never lands in a scratch file (F7 discipline).

```json
{ "database": "postgres",
  "postgres": { "host": "127.0.0.1", "port": 5432, "db": "ensemble_dev",
                 "user": "ensemble_dev_app", "password": "" },
  "sqlite":  { "instances_db": "./data/instances.db", "checkpoints_db": "./data/checkpoints.db" } }
```

The prod-DSN fence (`assert_prod_dsn_sanctioned`) did not fire: the resolved DSN name is `ensemble_dev`,
not `ensemble_prod`, and the boot identity marker is an explicit `dev`.

### 1.4 Boot attempt 2 — GREEN

| Item | Value |
|---|---|
| Binary | `/home/nea/agents-ensemble/releases/v0.31.34/ensemble-prod` (frozen, PyInstaller) |
| PID | **806181** (own session, `SID=806181`) — recorded before start |
| Port | **8079** (`DAEMON_PORT=8079` + `PORT=8079`; release `config.yaml` binds `daemon.port: ${PORT:-8088}`) |
| Boot start | `2026-11-22T20:46:55Z` (epoch 1795380415) |
| **Boot wall time** | **26 s** → green at `20:47:21Z` — **inside the 120 s Boot SLO** |
| Banner (verbatim) | `20:47:06 - daemon.api - INFO - Starting Ensemble v0.31.34` (and `20:47:07` — two startup sites) |
| livez | `200` — `{"status":"alive","uptime_seconds":6.491584777832031,"version":"0.31.34"}` |
| readyz | `200` — `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],…}}` |
| Boot sweep kills | **NONE** — zero `sigkill`/`killing`/`stale subprocess` lines in the log; only `maintenance boot sweep: 0 interrupted run(s)` (the unrelated checkpoint-cleanup section) |
| `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` | pinned → the 2026-10-31 live-process-kill incident structurally avoided |

---

## 2. CHAIN ARMED (design proof #1 — chain row verbatim)

The chain row is **boot-seeded** (`daemon/repositories/source/maintenance_seed.py`), so it did not exist in the
dev DB before this boot — it was created by the staged boot, which is itself part of the proof.

```
20:47:13 - daemon.repositories.source.maintenance_seed - INFO - [CentralMaintenance] seeded the weekly chain row
             067cdc8d-6b4e-5acf-8023-52fe1ee381db (run_at=2026-11-28T21:15:00+00:00)
20:47:13 - daemon.manager - INFO - [CentralMaintenance] chain guard: action=created source_id=067cdc8d-… run_at=2026-11-28T21:15:00+00:00 (chain row created)
```

**Row (verbatim, dev DB `ensemble_dev.source_configs`):**

```
source_id   | 067cdc8d-6b4e-5acf-8023-52fe1ee381db
source_type | scheduler
name        | central-maintenance-weekly
enabled     | t        autostart | t        status | running
created_at  | 2026-11-22T20:47:13.028568+00:00
updated_at  | 2026-11-22T20:48:20.925346+00:00
```

**Config JSON (verbatim, `jsonb_pretty`):**

```json
{
    "agent": "maintenancer",
    "chain": {
        "mode": "weekly-random",
        "weekday": 0,
        "timezone": "Asia/Ho_Chi_Minh",
        "run_at_utc": "2026-11-28T21:15:00+00:00",
        "window_end": "05:00",
        "window_start": "01:00"
    },
    "run_at": "2026-11-28T21:15:00+00:00",
    "message": "Weekly central maintenance run.\n\ntrigger: scheduled\n\nExecute the weekly maintenance run:\n1. Dispatch the five maintenance lane children (one per lane, per the\n   maintenancer orchestrator workflow).\n2. Collect the lane results and dispatch the report writer child with\n   them.\n3. Parse the `trigger:` token from this message body and pass the parsed\n   value as `trigger_type` in the writer child's dispatch envelope.\n",
    "timezone": "Asia/Ho_Chi_Minh",
    "local_time": "04:15",
    "project_id": "71931ae0-0f25-5fbf-853b-2a78cc978d7e",
    "recurrence": "once",
    "instance_mode": "new_instance"
}
```

**Knob assertions:**

| Assertion | Evidence | Verdict |
|---|---|---|
| `recurrence = once` | `"recurrence": "once"` | ✅ |
| `agent = maintenancer` | `"agent": "maintenancer"` | ✅ |
| chain block present (archive-aware shape) | `chain.mode=weekly-random` + window/weekday/tz/`run_at_utc` | ✅ |
| window knobs `01:00`–`05:00` ICT | `window_start=01:00`, `window_end=05:00`, `timezone=Asia/Ho_Chi_Minh` | ✅ |
| **random minute inside the window** | `local_time=04:15` → 04:15 ∈ [01:00,05:00), minute **:15** (not :00) | ✅ |
| window mapped to UTC = 18:00–22:00 **prior day** | `run_at_utc = 2026-11-28T21:15:00Z` → 21:15Z ∈ [18:00Z,22:00Z] of the prior UTC day = **2026-11-29 04:15 ICT** | ✅ |
| correct weekday | `weekday: 0` (Sunday) and `2026-11-28` (the UTC date of `run_at_utc`) **is** a Sunday | ✅ |
| chain row untouched by the MANUAL run | post-run: `run_at` still `2026-11-28T21:15:00+00:00` | ✅ (the manual one-shot is a separate row) |

> Note for the plan owner: the weekday knob is evaluated against the **UTC** date of `run_at_utc` while the
> window is a **local (ICT)** clock window — so a Sunday-weekday chain actually fires *Monday 04:15 ICT*.
> The knob pair is self-consistent and the P5A picker tests pass, but an operator reading "weekly, Sunday,
> 01:00–05:00" will expect a Sunday *local* run. Worth a one-line clarification in the operator docs (F2).

---

## 3. MANUAL TRIGGER — the exact documented invocation

### 3.1 The invocation (runbook verbatim → wire form)

The runbook's canonical line (`docs/runbooks/central-maintenance-manual-trigger.md:22`):

```
task_schedule(label="central-maintenance-manual-<ts>", message="trigger: manual\nExecute one manual Central Maintenance run now: dispatch the five lane children + report writer per the orchestrator workflow; parse the trigger token and pass trigger_type to the writer child.", when=<now ISO 8601>, recurrence="once", agent_id="maintenancer")
```

Executed through **this daemon's own API** (`POST /api/schedules` — the router's docstring: *"The body mirrors
the scheduling tool category's `task_schedule` semantics"*), REST field names mapped by the frozen router
(`label ← name`, `agent ← agent_id`, `when ← run_at` for `recurrence=once`):

**Request body (verbatim, `POST http://127.0.0.1:8079/api/schedules`):**

```json
{
  "source_id": "central-maintenance-manual-20261122T204859Z",
  "name": "central-maintenance-manual-20261122T204859Z",
  "agent_id": "maintenancer",
  "message": "trigger: manual\nExecute one manual Central Maintenance run now: dispatch the five lane children + report writer per the orchestrator workflow; parse the trigger token and pass trigger_type to the writer child.",
  "project_id": "71931ae0-0f25-5fbf-853b-2a78cc978d7e",
  "priority": 5,
  "instance_mode": "new_instance",
  "recurrence": "once",
  "run_at": "2026-11-22T20:48:59.055474+00:00"
}
```

Message bytes (repr, proving the token is on its **own line**):

```
'trigger: manual\nExecute one manual Central Maintenance run now: dispatch the five lane children + report writer per the orchestrator workflow; parse the trigger token and pass trigger_type to the writer child.'
```

**Response (verbatim, HTTP 201):**

```json
{"id":"ca41563d-795a-425e-bbcc-fa59f2b9ebd5","source_id":"ca41563d-795a-425e-bbcc-fa59f2b9ebd5","label":"central-maintenance-manual-20261122T204859Z","status":"starting","next_run_at_local":null,"next_run_at_utc":null,"tz_warning":""}
```

`project_id` = the **same** project the chain row uses (`__system_default__`, `71931ae0-0f25-5fbf-853b-2a78cc978d7e`)
— the scheduler needs a project for the instance-mapper lookup.

### 3.2 The FIRE — no idle gate (design proof #2, live)

```
20:48:59 - daemon.sources.registry - INFO - Creating adapter for ca41563d-795a-425e-bbcc-fa59f2b9ebd5: config={'agent': 'maintenancer', 'run_at': '2026-11-22T20:48:59.055474+00:00', 'message': 'trigger: manual\nExecute one manual Central Maintenance run now: …', 'timezone': 'Etc/UTC', 'local_time': '2026-11-22T20:48:59.055474+00:00', 'project_id': '71931ae0-…', 'recurrence': 'once', 'instance_mode': 'new_instance'}
20:48:59 - daemon.services.scheduling_service - INFO - scheduling_service: created schedule ca41563d-… (label='central-maintenance-manual-20261122T204859Z', agent=maintenancer, caller=/rest-api, started=True)
20:48:59 - daemon.sources.mapper - INFO - Creating new instance: source_id=ca41563d-…, external_user_id=ca41563d-…, agent_id=maintenancer
20:49:02 - daemon.sources.adapters.scheduler - INFO - One-time schedule executed: ca41563d-795a-425e-bbcc-fa59f2b9ebd5
```

**Execution row (verbatim, `GET /api/schedules/ca41563d-…/executions`):**

```json
{"execution_id":"7372f4c3-6660-4590-909f-fbd843c8306a","schedule_id":"ca41563d-795a-425e-bbcc-fa59f2b9ebd5","triggered_at":"2026-11-22T20:48:59.328197Z","instance_id":null,"status":"queued","error_message":null,"completed_at":"2026-11-22T20:49:02.522128Z"}
```

**Fire delta:** `run_at` (created) `20:48:59.055474Z` → `triggered_at` `20:48:59.328197Z` = **0.272723 s ≈ 273 ms**.

The one-shot fired **immediately** — no idle/busyness precondition, no waiting for a window, no deferral
(`_defer_count: 0`, `_defer_since: null` in the persisted row). The manual one-shot was consumed and the row
retired `status=stopped`, `enabled=f` (one-shot semantics), while the WEEKLY chain row was untouched.

**Manual row (verbatim, dev DB):**

```
source_id   | ca41563d-795a-425e-bbcc-fa59f2b9ebd5
source_type | scheduler
name        | central-maintenance-manual-20261122T204859Z
enabled     | f   autostart | t   status | stopped
created_at  | 2026-11-22T20:48:59.229920+00:00
updated_at  | 2026-11-22T20:49:02.558827+00:00
config      | { "agent": "maintenancer",
              |   "run_at": "2026-11-22T20:48:59.055474+00:00",
              |   "message": "trigger: manual\nExecute one manual Central Maintenance run now: …",
              |   "timezone": "Etc/UTC",
              |   "local_time": "2026-11-22T20:48:59.055474+00:00",
              |   "project_id": "71931ae0-0f25-5fbf-853b-2a78cc978d7e",
              |   "recurrence": "once", "_defer_count": 0, "_defer_since": null,
              |   "instance_mode": "new_instance", "_defer_message_id": null }
```

No `chain` block and **no `creator_instance_id`** — exactly the documented REST-origin shape (the runbook's
"REST-fallback caveat": REST rows carry no creator id). **It did not matter**: the runtime path is the
**message-body token**, and `trigger_type: manual` still round-tripped (see §5).

---

## 4. RUN EXECUTION — orchestrator, five lanes, writer

### 4.1 Timeline (from this daemon's `/api/instances` + boot log)

| Time (UTC) | Actor | Event |
|---|---|---|
| 20:48:59.328 | scheduler | one-shot **fires** (273 ms after `run_at`) |
| 20:49:02.183 | **orchestrator** `d83bfed4-e020-4bac-b6d0-e171288afccd` (agent=maintenancer) | spawned; title **"Manual Central Maintenance Run"**; `initiative_message` = the exact runbook string incl. `trigger: manual` |
| 20:49:20–21 | daemon | 6 skills cloned from the bank: `lane-disk-sweep`, `lane-pok-sweep`, `lane-skill-check`, `lane-abnormality-check`, `lane-metrics-watch`, `run-report-writer` |
| 20:51:25 | `explorer` | "Weekly Central Maintenance Dispatch" (orchestrator's first dispatch) → completed 20:53:00 |
| 20:53:57 | `explorer` | **lane-pok-sweep** → completed 20:55:46 |
| 20:53:58 | `worker` | **lane-disk-sweep** → completed ~21:12:14 |
| 20:53:58 | `explorer` | **lane-abnormality-check** → completed 21:03:45 |
| 20:56:18 | `explorer` | pok-sweep **re-dispatch 1/1** → completed (escape valve exhausted) |
| 20:57:27 | `explorer` | **lane-metrics-watch** → completed |
| 20:59:57 | `explorer` | **lane-skill-check** → completed |
| 21:13:28 | `worker` | **run-report-writer** ("Central Maintenance Run Report") → wrote report 21:18:51, receipt 21:19:31, INDEX last |
| 21:19:30 | — | `run-ckpt-20261122_211850382091-68589da3.md` present in the artifact root |

Orchestrator posture during the run: `waiting_children` (the maintained hold), and it closed after the writer.

### 4.2 Per-lane outcomes + FAILURE ISOLATION

| Lane | Child status | Recovered? |
|---|---|---|
| `pok-sweep` | **failed [incomplete]** (escape valve exhausted; AC10 scope refusal) | ❌ no — orchestrator seat is non-scoped too |
| `metrics-watch` | **failed (child)** | ✅ orchestrator-supplement recovered (2 calls) |
| `skill-check` | **failed (child)** | ✅ orchestrator-supplement recovered (census) |
| `abnormality-check` | **degraded** (DB passes supplemented from the maintenancer `ens-db` seat, SELECT-only) | partial |
| `disk-sweep` | **ok** (reads only; **ZERO deletions**, envelope forbade) | — |

**Failure-isolation HELD.** Three lanes failed and one was degraded, yet the writer still produced a full
report + receipt + INDEX. Overall status rolled up to **`degraded`** — not `failed`, not aborted.

**Watchdog:** `deadline: all five lanes resolved before deadline; NO watchdog/long-tool-nudge wake fired
(lanes resolved 20:55:04Z–21:12:14Z)`. The run spanned ~30 min against the ~2 h watchdog bound, so the
watchdog never had to decide — the designed failed-timeout path was NOT exercised (and need not be: this leg's
job was the trigger path + receipt shape).

**A defect surfaced by the live leg (not visible in Leg A):** `pok-sweep` failed because the child instance's
`spawn project_id` did not bind (the child resolved `__system_default__`) and `pok_query` refused
(`pok_default_project`, AC10). The orchestrator could not supplement it — the orchestrator seat is
non-scoped too. That is a **real, reproducible lane failure** the static matrix could not have caught, and it
is the top `needs_emperor_word` item in the receipt.

---

## 5. ARTIFACTS (AC8 four-surface live proof)

**Artifact root — PROMOTE-STABLE install root, NOT a release dir (as designed):**

```
/home/nea/agents-ensemble/.agents/maintenancer/RESULTS/
├── INDEX.md                                              812 B
├── evidence/                                            (per-run evidence)
├── receipt-ckpt-20261122_211850382091-68589da3.md     4 514 B
└── run-ckpt-20261122_211850382091-68589da3.md        11 145 B
```

sha256: `run…` `f6f30c598de0c0597c2b872ac6dd0e47df584e7708634f9c0406b3199b37d5cd` ·
`receipt…` `d6e7cf845dc0254d1cd28f14fb6b65d6eeff61d5982bf09babd3efb622089af0` ·
`INDEX.md` `f207d5b806135d2fa340352fa14871044e13bd9c1ad78faa6727245ad211e5b9`

**run_id shape:** `ckpt-20261122_211850382091-68589da3` matches the BR-17 regex `^ckpt-\d{8}_\d{12}-[0-9a-f]{8}\Z` ✅.

### 5.1 THE RECEIPT SHAPE (verbatim, full)

```markdown
# Run Receipt — ckpt-20261122_211850382091-68589da3

## Header

- run_id: `ckpt-20261122_211850382091-68589da3` (minted by report-writer, BR-14 primary rung — helper `daemon/services/maintenance_run_identity.py`)
- run_token: `run-manual-20261122T204859Z` (envelope linkage; INDEX dedup survives re-mints)
- trigger_type: `manual` (VERBATIM from envelope)
- started_at: 2026-11-22T20:48:59Z
- ended_at: 2026-11-22T21:19:31Z (report-writer write time)
- overall_status: `degraded`
- artifact root: `/home/nea/agents-ensemble/.agents/maintenancer/RESULTS/` (LIVE INSTALL v0.31.31, PostgreSQL)

## Lane status roll-up

| lane | status | measured_at | recovery |
|---|---|---|---|
| pok-sweep | failed [incomplete] | 2026-11-22T20:56:18Z (re-dispatch 1/1; first attempt 20:55:04Z) | none — escape valve exhausted; NO orchestrator supplement possible (orchestrator seat also non-scoped → same AC10 refusal) |
| metrics-watch | failed (child) — ORCHESTRATOR-SUPPLEMENT recovered | child 2026-11-22T20:58:53Z; supplement 21:00:05Z | digest recovered orchestrator-side (2 calls) |
| skill-check | failed (child) — ORCHESTRATOR-SUPPLEMENT recovered | child 2026-11-22T20:59:57Z; supplement ~21:03Z | census recovered orchestrator-side |
| abnormality-check | degraded | 2026-11-22T21:02:34Z | DB passes supplemented by maintenancer ens-db seat (SELECT-only, lexicographic TEXT cutoffs) |
| disk-sweep | ok | 2026-11-22T21:12:14Z | — (reads only; ZERO deletions, envelope forbade) |

Status tally: 1 ok, 1 degraded, 3 failed (2 recovered by orchestrator supplements, 1 unrecoverable) → overall `degraded`. All five lanes resolved before deadline; no watchdog/long-tool-nudge wake fired.

## Anomaly filings

- Orchestrator cluster `76f8c3d5` — 4 reports: d5d6cc03, f6a4384b, b80a615e, 34c37410 (tool-failure/major).
- Queue-scheduling cluster `5cbdc0eb` — queue-scheduling/major.
- Lane-filed `60f5a69b` — environment-config/minor.
- Recorded not filed: skill bank drift 28→29 (~21:01), outside run containment.

## Recommendations (three classes, never collapsed)

### auto_fix_now (1)

- (disk-sweep) empty-hbk 10 239 inodes → disk-sweep execution pass NEXT run (eligible, NOT executed).

### needs_emperor_word (6)

- (pok-sweep) fix instance→project resolution for pok_* in daemon-global dispatch (spawn project_id must bind before AC10 gate).
- (pok-sweep) decide whether POK read tools take an explicit project_id for maintenance sweeps. NO orchestrator supplement possible (orchestrator seat is also non-scoped → same AC10 refusal).
- (metrics-watch) bind metrics_read family to explorer children before next run (anomaly f6a4384b).
- (skill-check) provision skill_* reads on explorer surface or route skill-check to a capable agent (anomaly b80a615e).
- (abnormality-check) reportless-week residual: zero run artifacts from ANY prior fire on the live root; audit-policy decision (surfaced, not self-healed, not re-fired).
- (disk-sweep) populated-hbk retention; pytest-of-nea keep policy; node-compile-cache wipe; base-extract retention; v0 sidecars.

### route_to_lane (10)

- (disk-sweep) stale shakedown-fleet (182) → minting source.
- (disk-sweep) stale skill-capture-sink (102) → minting source.
- (disk-sweep) stale _MEI → giter.
- (disk-sweep) mktemp trees → disk-sweep/giter.
- (disk-sweep) base-extract → gate/verify lanes.
- (disk-sweep) pytest roots (34/37) → test lane.
- (disk-sweep) worktree drift → giter lane.
- (disk-sweep) journal .bak → owning lane.
- (abnormality-check) w3.py TypeError fix → code owner (anomaly 34c37410).
- (abnormality-check) guard-blocked eligible task → queue owner (anomaly 5cbdc0eb).

Routing legend: `auto_fix_now` = safe, bounded, in-lane authoring already completed. `needs_emperor_word` = a decision the report surfaces but never makes. `route_to_lane` = a fix owned by a lane or by the operator.

## Evidence (ABSOLUTE paths)

- abnormality lane: `/home/nea/agents-ensemble/.agents/maintenancer/RESULTS/evidence/run-manual-20261122T204859Z/watch-lane-abnormality-check.md`
- disk-sweep lane (12 files): `/home/nea/ensemble-src/.agents/maintenancer/RESULTS/evidence/run-manual-20261122T204859Z/` — metrics-summary.txt, hbk-breakdown.txt, hbk-legacy.txt, hbk-legacy2.txt, hbk-sizes.txt, hbk-recency.txt, scratch-classes.txt, recency.txt, protect-list.txt, open-files-tmp.txt, open-paths.txt, worktree-drift.txt

Full lane detail (verbatim PD-4a blocks): `run-ckpt-20261122_211850382091-68589da3.md` (same directory).
```

**Receipt shape summary (for the final report's manual-trigger documentation):**

| Element | Shape | Proven |
|---|---|---|
| `run_id` | `ckpt-YYYYMMDD_HHMMSSffffff-hex8` | ✅ minted by the writer (BR-14 rung 1) |
| `run_token` | `run-manual-<trigger ts>` | ✅ envelope linkage; makes INDEX dedup survive writer re-mints |
| `trigger_type` | `manual` | ✅ **round-tripped from the message-body token**, not derived, not read from a DB |
| `started_at` / `ended_at` | ISO-8601 Z | ✅ `20:48:59Z` (the trigger) → `21:19:31Z` (the writer) |
| `overall_status` | `degraded` | ✅ roll-up of the lane tally (1 ok / 1 degraded / 3 failed) |
| lane blocks | **exactly five** PD-4a blocks (pok-sweep, metrics-watch, skill-check, abnormality-check, disk-sweep) | ✅ |
| per-lane fields | `status`, `measured_at`, `recovery` (+ full PD-4a detail in the run file: `summary`, `metrics`, `actions_taken`, `recommendations`, `evidence`) | ✅ |
| recommendation classes | `auto_fix_now` (1) / `needs_emperor_word` (6) / `route_to_lane` (10) — **never collapsed** | ✅ |
| artifact root | `/home/nea/agents-ensemble/.agents/maintenancer/RESULTS/` (LIVE INSTALL) | ✅ AC8 |

### 5.2 The report's summary section (verbatim)

```
- run_id: `ckpt-20261122_211850382091-68589da3` (minted by report-writer, BR-14 primary rung: `/home/nea/ensemble-src/.venv/bin/python` → `daemon/services/maintenance_run_identity.py:new_maintenance_run_id()`)
- run_token: `run-manual-20261122T204859Z` (envelope linkage — recorded in run, receipt, and INDEX row so dedup survives re-mints)
- trigger_type: `manual` (VERBATIM from envelope; never derived, never read from a DB)
- started_at: 2026-11-22T20:48:59Z (manual trigger; one-shot task_schedule arm 20:32:52Z)
- ended_at: 2026-11-22T21:18:51Z (report-writer write time)
- overall_status: `degraded` — 1 ok, 1 degraded, 3 failed; two failed lanes recovered by orchestrator supplements, one unrecoverable
- deadline: all five lanes resolved before deadline; NO watchdog/long-tool-nudge wake fired (lanes resolved 20:55:04Z–21:12:14Z)
- writer: run-report-writer (L0 rung); write order honored: run → receipt → INDEX last
- artifact root: `/home/nea/agents-ensemble/.agents/maintenancer/RESULTS/` — LIVE INSTALL (v0.31.31, PostgreSQL; see `/home/nea/agents-ensemble/data/ensemble.json`). `/home/nea/ensemble-src` is the SOURCE CHECKOUT (venv + run-identity helper + this run's disk-sweep evidence live there). Root correction proven this run by lane-abnormality-check.
```

**Five PD-4a lane blocks present in the run file** (each with `summary`, `metrics`/`actions_taken`,
`recommendations`, evidence path): `pok-sweep`, `metrics-watch`, `skill-check`, `abnormality-check`, `disk-sweep` ✅.

**INDEX.md (verbatim):**

```markdown
# Central Maintenance Run INDEX

Ledger of weekly-run top-level artifacts at this root (LIVE INSTALL `/home/nea/agents-ensemble`). One row per run_id; appended by run-report-writer after run → receipt. `run_token` is recorded per row so dedup survives report-writer re-mints. Evidence roots are per-run and may be split across the live install and the source checkout (`/home/nea/ensemble-src`) — see the run file for the verbatim split.

| run_id | run_token | trigger_type | started_at | ended_at | overall_status | run_file | receipt_file |
|---|---|---|---|---|---|---|---|
| ckpt-20261122_211850382091-68589da3 | run-manual-20261122T204859Z | manual | 2026-11-22T20:48:59Z | 2026-11-22T21:19:31Z | degraded | run-ckpt-20261122_211850382091-68589da3.md | receipt-ckpt-20261122_211850382091-68589da3.md |
```

Write order honored: **run → receipt → INDEX last** ✅.

---

## 6. LIST DOOR

```
$ curl -s -w '\nHTTP %{http_code} content-type=%{content_type}\n' http://127.0.0.1:8079/api/maintenance-reports
{"detail":{"error":"AUTH_NOT_CONFIGURED","message":"Auth not configured (fail-closed)"}}
---HTTP 503--- content-type=application/json
```

**This is the door's designed fail-closed posture, not a defect** (`daemon/routers/maintenance_reports.py:158-171`):
`create_app` sets `app.state.auth_config` ONLY when auth is enabled; with auth OFF (the shipped default) the door
refuses to publish the run history on an open `0.0.0.0` bind. The staged daemon had auth OFF because the
scrub removed the ambient `ENSEMBLE_AUTH_*` vars and the frozen release's `config.yaml` ships `enabled: false`.
Enabling auth locally needs an external OIDC IdP (the live install's `ENSEMBLE_AUTH_*` values are fenced out of
this leg by the livesafety brief) — so the door's **auth-ON** path could not be exercised here.

**The FE-facing view URLs DO serve the artifacts** (this is the surface the FE renders):

```
GET /views/maintenance-reports/run-ckpt-20261122_211850382091-68589da3.md
  → HTTP 200, content-type=text/html; charset=utf-8, 14 502 bytes
  → <title>run-ckpt-20261122_211850382091-68589da3.md - live view</title>
GET /views/maintenance-reports/receipt-ckpt-20261122_211850382091-68589da3.md
  → HTTP 200, content-type=text/html; charset=utf-8, 7 840 bytes
  → <title>receipt-ckpt-20261122_211850382091-68589da3.md - live view</title>
```

The `maintenance-reports` live-view root was registered at boot: `[LiveViews] subsystem ready: enabled=True
roots=['designer-artifact', 'maintenance-reports', 'planning', 'reports', 'tmp-images', 'video-artifact']` ✅.
The `.md` renderer wraps the artifact in the live-views HTML shell (FE-renderable shape, API-level).

---

## 7. AFTER-PICTURE + /tmp discipline

| Metric | PRE | POST | Verdict |
|---|---|---|---|
| `ensemble-hbk-*` files | 13 459 | **13 459** | ✅ identical |
| `ensemble-hbk-*` bytes | 51 310 | **51 310** | ✅ identical |
| `ensemble-hbk-*` **populated** (>0 B) | 308 | **308** | ✅ **ALL POPULATED FILES RETAINED** |
| `/tmp/gate-hbk-base*` bytes | 357 808 776 | **357 808 776** | ✅ identical |
| `/tmp/gate-hbk-base*` inodes | 19 194 | **19 194** | ✅ identical |
| `/tmp` total bytes | 52 220 860 451 | 52 380 993 051 | +160 132 600 |
| `/tmp` total inodes | 2 151 686 | 2 152 967 | +1 281 |

**Delta attribution:** this leg's own scratch footprint is **7 580 881 B / 14 inodes**
(`/tmp/p5b-20261122T204234Z`). The remainder (+152.5 MB / +1 267 inodes) is **other lanes' traffic on this
host** (`/tmp/pytest-of-nea` 3.88 GB, `/tmp/flipprep-leg4` 958 MB, `/tmp/vkey-emulator.log`, kotlin-daemon
logs, `/tmp/wt-list.txt`) — **not** this run. The disk lane itself performed **ZERO deletions**
(`actions_taken: none (reads only)`, "envelope forbade"), and the hbk classes + gate-hbk-base are
byte-for-byte identical, which is the proof that the empty-hbk sweep was **eligible but NOT executed**
(`auto_fix_now … eligible, NOT executed`).

---

## 8. Shutdown

```
$ tr '\0' ' ' < /proc/806181/cmdline
/home/nea/agents-ensemble/releases/v0.31.34/ensemble-prod        ← exact-target verified BEFORE kill
$ kill -TERM 806181      (21:22:39Z)
GONE after 3s ; confirmed gone
$ ss -ltn | grep ':8079'   → (my port 8079 free)
```

Clean SIGTERM exit in **3 s** (no SIGKILL needed). Daemon log tail: `Cleaning up resources… / Database engine
disposed / Graceful shutdown complete`. The live daemon (PID 268357, `…/current/ensemble-prod`) was **never
touched** — still running at 4 h 21 m uptime after the leg.

**No orphan kills** in the boot log (zero `sigkill`/`killing`/`stale subprocess` lines) — the pinned
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` did its job.

---

## 9. Live-untouched evidence (state.json)

| Field | PRE | POST | Verdict |
|---|---|---|---|
| `current` | `v0.31.31` | **`v0.31.31`** | ✅ unchanged |
| `previous` | `v0.31.27` | `v0.31.27` | ✅ |
| `in_flight` | `null` | `null` | ✅ |
| `quarantined` | `["v0.28.5","v0.31.8"]` | `["v0.28.5","v0.31.8"]` | ✅ |
| `cooldown_until` / `pending_op` / `pending_restart` | `null` / `null` / `null` | `null` / `null` / `null` | ✅ |
| `pending_actions` / `pending_wakes` | `{}` / `{}` | `{}` / `{}` | ✅ |
| `rollback_window_count` | `{"24h":0,…}` | `{"24h":0,…}` | ✅ |
| `current` symlink | `releases/v0.31.31` | `releases/v0.31.31` | ✅ |
| **file sha256** | `b2792e90dbfb13eeb177c2215210894a7a7a31c39722f50c293d467ac6a17a48` | `1589a06edf242c86a3d5b460b2c6da6fc89d141e70574ad03fa626cd28b02e00` | ⚠️ **differs** — see below |

**Why the file bytes differ, proven:** the LIVE daemon is still running and appends its own journal events.
Every history entry added during the leg is an `mcp_server_state` event (context7 flap/recover pairs — the
POST file carries 304 history entries, the last 12 of which are all `mcp_server_state`). Occurrence counts
inside the whole history blob for this leg's identifiers: `maintenance` **0**, `ca41563d` **0**, `d83bfed4`
**0**, `8079` **0**, `run-manual` **0** — **zero** mentions of this run. (`0.31.34` appears once, in the
PRE-existing `stage-scripts-advance` entry at 2026-11-22T20:29:54Z, which was already present in the PRE read.)

**Whole-install write-window audit** (every file under the live install touched between the boot start
`20:46:55Z` and the shutdown `21:22:41Z` — 11 entries, all accounted for):

| Path | mtime | Owner |
|---|---|---|
| `.agents/` , `.agents/maintenancer/` | 20:47:12 | **mine** — the seeder's idempotent artifact-root creation (AC8) |
| `.agents/maintenancer/RESULTS/evidence/run-manual-…/watch-lane-abnormality-check.md` | 21:03:01 | **mine** — the abnormality lane's evidence (sanctioned root) |
| `.agents/maintenancer/RESULTS/run-ckpt-….md` | 21:19:25 | **mine** — the writer's report (sanctioned root) |
| `.agents/maintenancer/RESULTS/receipt-ckpt-….md` | 21:19:49 | **mine** — the writer's receipt (sanctioned root) |
| `.agents/maintenancer/RESULTS/INDEX.md` | 21:19:55 | **mine** — the writer's ledger row (sanctioned root) |
| `releases/state.json` | 21:10:25 | **the LIVE daemon's own journal append** (mcp_server_state only — see above) |
| `releases/` (dir mtime, from the state.json write) | 21:10:25 | live daemon |
| `current` (symlink) | **17:01:16** | **untouched — before this leg** |

**No `data/` file was touched** (the scratch `ENSEMBLE_DATA_DIR` + scratch cwd fences held), and no
`docs/`, `agents/`, `config.yaml`, `launcher.sh` or `manifest.json` write occurred.

**Conclusion: the live install's promote state is byte-for-byte unchanged in every promote-relevant field; the
file-level delta is the live daemon's own concurrent journal appends.**

---

## 10. Findings (carried to the final report)

| # | Finding | Severity | Owner |
|---|---|---|---|
| **F1** | **The staged release does not ship `docs/`.** `releases/v0.31.34/` contains `agents/ daemon/ config.yaml frontend/ guidelines/ plugins/ scripts/ manifest.json launcher.sh` — but **no `docs/runbooks/`**, so the canonical manual-trigger runbook is NOT in the release artifact. This leg had to read the branch worktree copy (the dispatch brief's sanctioned fallback). | **major** (operator-facing deliverable absent from the artifact) | release lane / stage.sh manifest |
| **F2** | The chain's `weekday` knob is evaluated against the **UTC** date of `run_at_utc` while the window is a **local ICT** clock window — a "Sunday 01:00–05:00" chain actually fires **Monday 04:15 ICT** (2026-11-28T21:15Z = 2026-11-29 04:15 ICT). Self-consistent with the P5A picker, but operator-surprising. | minor (doc clarity) | plan / operator docs |
| **F3** | **Lane evidence roots SPLIT across two roots.** The report + receipt + INDEX landed in the LIVE INSTALL root (AC8 ✅), but the **disk-sweep lane wrote its 12 evidence files to `/home/nea/ensemble-src/.agents/maintenancer/RESULTS/evidence/…`** (the SOURCE CHECKOUT) while the **abnormality lane wrote to the install root**. Root resolution for a *lane child* is agent/cwd-dependent, unlike the writer's. Both paths are recorded absolutely in the receipt (verbatim, nothing moved) — but the "PROMOTE-STABLE root" guarantee currently holds only for the three top-level artifacts. | **major** (AC8 surface-split; the disk lane's evidence would be lost by a checkout reset) | maintenance-seed root resolver / lane prompt |
| **F4** | **`GET /api/maintenance-reports` returns 503 `AUTH_NOT_CONFIGURED` on any auth-OFF deployment** (the shipped default). The FE Report page cannot read the run list unless the deployment enables auth. The door's fail-closed posture is *correct* (BR-17), but it means the AC8 list-door surface is only reachable on an auth-enabled install. | **major** (AC8 list door unreachable by default) | FE + auth posture decision |
| **F5** | `pok-sweep` **fails on the live leg**: the spawned child's `project_id` does not bind (child resolved `__system_default__`), `pok_query` refuses `pok_default_project` (AC10), the re-dispatch escape valve is exhausted, and the orchestrator seat is non-scoped too — so **no** orchestrator supplement is possible. Real, reproducible, invisible to the static matrix. | **major** (lane non-functional) | instance→project resolution for `pok_*` in daemon-global dispatch |
| **F6** | `metrics-watch` and `skill-check` failed as *children* (the `metrics_read` / `skill_*` families are absent from the `explorer` child surface) and were rescued by orchestrator supplements — i.e. the run's correctness currently depends on the orchestrator seat's broader tool surface. | major | agent seat / tool registration |
| **F7** | The run report's parenthetical `(manual trigger; one-shot task_schedule arm 20:32:52Z)` carries a **timestamp that matches neither the trigger (20:48:59Z) nor the boot (20:47:13Z)** — a mis-derived provenance note inside the artifact. The receipt's `started_at` (20:48:59Z) is correct. | minor | report-writer provenance line |
| **F8** | The staged boot's **first** attempt is refused by the rogue-containment tier-2 guard unless `ENSEMBLE_DB_AMBIENT_ALLOW=1` is set. A staged/frozen boot against an env-configured PG DB needs the tier-1 `ensemble.json` (or the allow flag) — an operator following "just boot the binary" hits a hard exit-78. The remedy is documented in the guard's own message, so it is not a defect — but it belongs in the staged-boot runbook. | minor (doc) | staged-boot runbook |

---

## 11. Reproduce

```bash
# 1. boot the frozen binary with the scrubbed env (see §1.2 for the exact set/scrub lists)
P5B_SCRATCH=/tmp/p5b-<ts> P5B_PIDFILE=$SCRATCH/daemon.pid \
  setsid bash -c 'exec bash /tmp/p5b-boot.sh' > /tmp/p5b-boot-<ts>.log 2>&1 < /dev/null &
# 2. tier-1 DB config (mode 600, NO password — env wins)
cat > $SCRATCH/data/ensemble.json <<'JSON'
{ "database": "postgres",
  "postgres": {"host":"127.0.0.1","port":5432,"db":"ensemble_dev","user":"ensemble_dev_app","password":""},
  "sqlite": {"instances_db":"./data/instances.db","checkpoints_db":"./data/checkpoints.db"} }
JSON
# 3. the canonical manual trigger (the runbook's invocation, byte-exact)
curl -s -X POST http://127.0.0.1:8079/api/schedules -H 'Content-Type: application/json' --data-binary @payload.json
# 4. read the receipt
cat /home/nea/agents-ensemble/.agents/maintenancer/RESULTS/receipt-<run_id>.md
```

**Scratch artifacts (this leg):** `/tmp/p5b-boot-20261122T204234Z.log` (attempt 1, the refusal),
`/tmp/p5b-boot-20261122T204234Z-attempt2.log` (the green boot, 175 KB),
`/tmp/p5b-20261122T204234Z/` (scratch data/logs/cwd + `ensemble.json`),
`/tmp/p5b-env-probe.sh` + `/tmp/p5b-env-probe-<pid>.log` (the env proof),
`/tmp/p5b-boot.sh` (the launcher), `/tmp/p5b-trigger-payload.json` (the exact request body).

---

## 12. Verdict

✅ **PASS.** The exact trigger path is proven in staging, end-to-end, on a real boot of the frozen `v0.31.34`
binary against the dev DB:

1. the boot is **green** (banner `Starting Ensemble v0.31.34`, livez + readyz 200, **26 s** wall, inside the 120 s SLO);
2. the chain row is **armed by the boot itself** with a random minute inside the knob window, the right weekday and the archive-aware chain block;
3. the documented one-shot invocation **fires 273 ms after `run_at` with no idle gate**;
4. the orchestrator + **all five lanes** + the writer run, with **failure-isolation holding** (3 lane failures, report still written) and the watchdog never firing;
5. **report + receipt + INDEX land in the PROMOTE-STABLE install root** (AC8 ✅), the receipt carries
   **`trigger_type: manual` round-tripped from the message-body token**, the five PD-4a lane blocks and the
   three recommendation classes;
6. the `/tmp` picture is **byte-identical** across the run (populated hbk files all retained);
7. the daemon shut down cleanly on its exact PID and the **live install's promote state is unchanged**.

**Carried:** F1 (runbook not shipped in the release), F3 (lane evidence-root split), F4 (list door 503 on
auth-OFF), F5 (pok-sweep lane non-functional), F6 (metrics/skill lanes need orchestrator supplements),
plus F2/F7/F8 (documentation-level).
---

# ADDENDUM — v0.31.35 targeted re-proof (2026-11-22)

> Appended 2026-11-22 ~23:45Z. Same livesafety frame as the leg above. Scope: live verdicts on the
> fix-wave claims (commit `cf4db3d20 fix(maintenance): live-proof cures — dispatch context, tool families,
> evidence root, local weekday`), staged on `v0.31.35` (4-way sha `e32bd3d1…`, source_commit `b1833127e`).
> Branch head at proof time: `cf4db3d20` (2 commits ahead of the leg-B commit `9db7f20fc`).

## A0. Boot evidence (v0.31.35)

| Item | Value |
|---|---|
| Binary | `/home/nea/agents-ensemble/releases/v0.31.35/ensemble-prod` (4-way sha verified `e32bd3d1…`) |
| PID / port | **959147** (own session) / **8079** (pre-checked free) |
| Banner | `22:36:19 - daemon.api - INFO - Starting Ensemble v0.31.35` (and `22:36:20`) |
| livez | `200` `{"status":"alive","uptime_seconds":11.848…,"version":"0.31.35"}` |
| readyz | `200` `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},…}` |
| **Wall time** | **30 s** (22:36:07Z → green 22:36:37Z) — inside the 120 s SLO (leg-B .34 boot was 26 s; comparable) |
| Boot attempt 1 | none — tier-1 `ensemble.json` persisted before boot (mode 600, no password) → clean tier-1 pass, no exit-78 |
| Orphan kills | none |

## A1. Trigger + fire delta

Byte-exact canonical string (unchanged runbook) → `POST /api/schedules` → 201, source_id `26d0c191-42bb-4055-8dc8-1752076ac44a`,
label `central-maintenance-manual-20261122T223657Z`, run_at `22:36:57.080264Z`.
Execution: triggered_at `22:36:57.244045Z` → **fire delta 0.163781 s ≈ 164 ms**, `status=triggered`, `_defer_count=0` → no idle gate ✅.

## A2. Per-fix verdicts (LIVE)

| # | Claim (fix wave `cf4db3d20`) | Verdict | Evidence |
|---|---|---|---|
| **F5** — pok lane runs with project context; no `__system_default__`, no AC10 refusal loop; completes with a PD-4a block | ✅ **PASS** | Receipt: `pok-sweep \| ok \| 2026-11-22T22:43:11Z \| — (read-only census; no supplement needed)`. Evidence `pok-census-llmproxy.md`: 8 active entries queried (`pok_query × 7`) under project `llmproxy (3f401aa9)` — no refusal, no incomplete, no escape-valve exhaustion. (`pok_query` now resolves against the lane's bound project instead of `pok_default_project`.) |
| **F6** — `metrics-watch` + `skill-check` run on family-matched members without orchestrator supplements (zero supplement calls) | ✅ **PASS** | Receipt: `skill-check \| ok \| 22:44:28Z \| — (read-only census + read-only bash; no supplement needed)`. `metrics-watch \| degraded \| 22:45:45Z \| digest produced from **designed seat supplement** (22:39:58Z) + cross-run deltas; REST door fail-closed (503)` — the supplement used is the **designed** ens-db seat, not an orchestrator rescue (contrast leg-B `.34` where the orchestrator had to rescue metrics-watch + skill-check). No orchestrator-supplement calls for either lane. |
| **F3** — ALL lane evidence lands under the PROMOTE-STABLE install root; no cwd-relative split | ❌ **FAIL** | Top-level triad (run/receipt/INDEX) landed in the **install root** ✅ (`/home/nea/agents-ensemble/.agents/maintenancer/RESULTS/`), but **every lane's evidence landed in the RELEASE-SCOPED root** `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/manual-20261122-223657/` (pok, metrics, skill-check, abnormality, disk-sweep + 22 raw dumps). The receipt itself records the release-scoped absolute paths and even carries a recommendation to "re-home this run's evidence before v0.31.35 eviction (F-2)". The promote-stable guarantee still holds only for the three top-level artifacts; lane evidence is evictable by keep-3. The `.34` leg had the SAME split (disk-sweep → source checkout); `.35` moved it to the release dir — a different split, not a fix. |
| **F2** — weekday semantics (my leg-B mislabel corrected) | ✅ **PASS** (correction of record) | Live row: `run_at_utc = 2026-11-28T21:15:00+00:00` → **Saturday in UTC, Sunday 04:15 ICT**. The weekday knob `0` = Sunday and the local date IS Sunday. My leg-B report's "Monday 04:15 ICT" was a mislabel; the fix wave's operator note is correct. Single ownership: the fix wave owns the picker proof, this addendum owns the correction of record. |
| **F1** — runbook shipped in the release (`docs/`) | ❌ **STILL OPEN** | `releases/v0.31.35/` has `agents/ daemon/ config.yaml frontend/ guidelines/ plugins/ scripts/ manifest.json launcher.sh` — **no `docs/`**. The canonical runbook was again read from the branch worktree copy. Runbook grew 4779 → 6840 B (the fix wave added the P5B operator notes — F4 fail-closed, tier-2 guard, weekday semantics — but did not move the file into the artifact). |

## A3. RECEIPT (verbatim, full)

```markdown
# Run Receipt — ckpt-20261122_232904787597-628caab9

## Header

- run_id: `ckpt-20261122_232904787597-628caab9` (minted by report-writer, BR-14 primary rung — helper `daemon/services/maintenance_run_identity.py`)
- run_token: `manual-20261122-223657` (envelope linkage; INDEX dedup survives re-mints)
- trigger_type: `manual` (VERBATIM from envelope)
- started_at: 2026-11-22T22:36:57Z
- ended_at: 2026-11-22T23:29:14Z (report-writer write time)
- overall_status: `degraded`
- artifact root: `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/` (LIVE INSTALL v0.31.33, PostgreSQL)

## Lane status roll-up

| lane | status | measured_at | recovery |
|---|---|---|---|
| pok-sweep | ok | 2026-11-22T22:43:11Z | — (read-only census; no supplement needed) |
| metrics-watch | degraded | 2026-11-22T22:45:45Z | digest produced from designed seat supplement (22:39:58Z) + cross-run deltas; REST door fail-closed (503 AUTH_NOT_CONFIGURED) |
| skill-check | ok | 2026-11-22T22:44:28Z | — (read-only census + read-only bash; no supplement needed) |
| abnormality-check | degraded | 2026-11-22T22:48:20Z | DB passes supplemented by maintenancer ens-db seat (SELECT-only, lexicographic TEXT cutoffs) |
| disk-sweep | degraded | 2026-11-22T23:22:00Z | — (read-only census; 0 bytes / 0 inodes reclaimed by design) |

Status tally: 2 ok, 3 degraded, 0 failed, 0 incomplete → overall `degraded`. All five lanes resolved before deadline; no watchdog/long-tool-nudge wake fired.

## Anomaly filings

- Child anomaly `4198843b` (metrics-watch, cluster `47ddf14c`) — REST door fail-closed / heartbeat lead.
- Manager-churn anomaly `d6fe8a3d` (cluster `1cc9b52b`) — one send-refusal window, one completion wake lost and recovered via COMPLETED-revive re-delivery (metrics), one duplicate lane execution (F-12), pok row terminal-flip lag (c699eacf 'running' 42m post-report, health-clean).
- Escape-valve re-dispatches: 0.
- Fleet hygiene (operator, untouched per containment): 1 zombie-pending, 11 orphans, 22 never-dispatched husks.
- Recorded not filed: skill bank drift watch (bank_clones without on-disk bodies), 2/83 requirement_json presence.

## Recommendations (three classes, never collapsed)

### auto_fix_now (2)

- (metrics-watch) read-only re-read of F9/F13/F4/F5/F23 current values and heartbeat-age driver at next digest when the door is available (NOT executed this window: door closed).
- (disk-sweep) empty-hbk 10,266 inodes → disk-sweep execution pass NEXT run (eligible, NOT executed).

### needs_emperor_word (13)

- (pok-sweep) F4 content-scope mismatch adjudication — 3/5 POK bodies use ensemble-platform vocabulary; ownership belongs to leader Phase-1.5 gate.
- (pok-sweep) suggested captures — environment-domain entries, implementation-audience entries, a curated deploy-verification procedure.
- (pok-sweep) verification debt — verify_count=0 / last_verified=null across all 5 active rows.
- (metrics-watch) enable auth on the daemon metrics door with a dedicated metrics:read machine token (setting/policy change, out of lane).
- (metrics-watch) decide priority for the recurring w3.py F18 wiring defect (F18 series_absent is DEFECT-driven, not benign).
- (skill-check) provision skill_* reads on explorer surface or route skill-check to a capable agent (anomaly b80a615e).
- (skill-check) confirm skill_bank=83 (seat) vs prior 16 discrepancy with seat owner.
- (skill-check) decide whether bank-clones (install-opendesign v1.3.0, opendesign-verify v1.0.0) need on-disk body mirrors.
- (abnormality-check) reportless-week residual — zero run artifacts from ANY prior fire on the release root; audit-policy decision (surfaced, not self-healed, not re-fired).
- (disk-sweep) re-home this run's evidence before v0.31.35 eviction (F-2) — re-home is needs_emperor_word; top-level artifacts written to install root only.
- (disk-sweep) .agents payload allowlist / manifest pin (F-3).
- (disk-sweep) populated-hbk retention; pytest-of-nea keep policy; node-compile-cache wipe; base-extract retention; v0 sidecars.
- (disk-sweep) worktree disposition (11 dirty of 38).

### route_to_lane (18)

- (metrics-watch) w3.py:234/240 F18 defect → code owner / abnormality lane.
- (metrics-watch) heartbeat_age 1054s driver identification → queue/worker lane.
- (metrics-watch) DB dead_letter_items=7 vs gauge=0 measure split → queue lane (lead only).
- (skill-check) requirement_json presence verification (2/83) → seat owner.
- (skill-check) usage-growth delta baseline capture → next run (first measured run).
- (skill-check) orphan-sweep residue → seat owner.
- (abnormality-check) w3.py TypeError fix → code owner (anomaly 34c37410).
- (abnormality-check) guard-blocked eligible task → queue owner (anomaly 5cbdc0eb).
- (abnormality-check) heartbeat_age 1054s driver identification → queue/worker lane.
- (abnormality-check) DLQ table/gauge measure split → queue lane.
- (disk-sweep) stale shakedown-fleet (534) → minting source.
- (disk-sweep) stale skill-capture-sink (607) → minting source.
- (disk-sweep) stale _MEI → giter.
- (disk-sweep) mktemp trees → disk-sweep/giter.
- (disk-sweep) base-extract → gate/verify lanes.
- (disk-sweep) pytest roots (34/37) → test lane.
- (disk-sweep) worktree drift → giter lane.
- (disk-sweep) journal .bak → owning lane.

Routing legend: `auto_fix_now` = safe, bounded, in-lane authoring already completed. `needs_emperor_word` = a decision the report surfaces but never makes. `route_to_lane` = a fix owned by a lane or by the operator.

## Evidence (ABSOLUTE paths)

- pok-sweep lane: `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/manual-20261122-223657/lane-pok-sweep/pok-census-manual-20261122-223657.md` + `pok-query-raw-manual-20261122-223657.json`
- metrics-watch lane: `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/manual-20261122-223657/lane-metrics-watch/rest-door-probe.txt` + `metrics-digest.md`
- skill-check lane: `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/manual-20261122-223657/lane-skill-check/01-census-skill-list.txt`, `02-set-equality-versions-autoload.txt`, `03-load-failures-and-db-seat-measures.txt`
- abnormality-check lane: `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/manual-20261122-223657/lane-abnormality-check/anomaly-sweep-and-ledger-watch.md`
- disk-sweep lane (1 report + 22 raw dumps): `/home/nea/agents-ensemble/releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/manual-20261122-223657/lane-disk-sweep/disk-sweep.md` + `disk-sweep-raw/`

Full lane detail (verbatim PD-4a blocks): `run-ckpt-20261122_232904787597-628caab9.md` (same directory).
```

## A4. List-door accumulation (AC8 live story)

Install-root `INDEX.md` now carries **two rows** (append-only, dedup by run_id):

```
| ckpt-20261122_211850382091-68589da3 | run-manual-20261122T204859Z | manual | 2026-11-22T20:48:59Z | 2026-11-22T21:19:31Z | degraded | … | … |
| ckpt-20261122_232904787597-628caab9 | manual-20261122-223657      | manual | 2026-11-22T22:36:57Z | 2026-11-22T23:29:14Z | degraded | … | … |
```

The new run appears alongside the leg-B proof run — the stable root accumulates. `GET /api/maintenance-reports` still returns **503 `AUTH_NOT_CONFIGURED`** (auth OFF — same posture as leg B). The FE-facing view URLs serve both runs' artifacts (`/views/maintenance-reports/run-ckpt-….md`, `receipt-ckpt-….md` → 200).

## A5. Shutdown + after

- SIGTERM PID 959147 → **gone in 12 s** (no SIGKILL), port 8079 free, no orphan processes; live daemon (PID 939102) untouched at 1 h 12 m uptime.
- `/tmp` spot check: `ensemble-hbk-*` 13 459 / 51 310 B / **308 populated retained** (unchanged); `gate-hbk-base*` 357 808 776 B / 19 194 inodes (unchanged); zero deletions.
- **state.json live-untouched:** promote fields `current=v0.31.33`, `previous=v0.31.31`, `in_flight=null`, `quarantined=[v0.28.5,v0.31.8]`, `pending_*/cooldown_until=null`. Occurrence counts for this run's identifiers (`26d0c191`, `3a267faf`, `959147`, `8079`, `manual-20261122-223657`) = **0 each**. sha256 `5daec83b…` (vs PRE `a5ade560…`) — the delta is the live daemon's own journal appends (a live promote to `v0.31.33` committed at 22:24:30Z, external to this leg; promote fields unchanged by *this* run).

## A6. Addendum verdict

- **F5 → PASS** (pok lane runs, 8-entry census, no AC10 refusal).
- **F6 → PASS** (metrics-watch + skill-check run on family-matched members; zero orchestrator supplements — only the designed ens-db seat).
- **F3 → FAIL** (lane evidence still NOT in the promote-stable root — it lands in `releases/v0.31.35/.agents/maintenancer/RESULTS/evidence/…`, release-scoped and evictable; top-level triad is in the install root. The receipt's own `artifact root` prose + a `needs_emperor_word` recommendation acknowledge the re-home debt.)
- **F2 → PASS** (correction of record — my leg-B "Monday" mislabel; local date is Sunday).
- **F1 → STILL OPEN** (`.35` ships no `docs/`).

**Honest caveat:** the first re-proof attempt (boot 22:19:30Z, PID 934650) was **killed mid-run at 22:22:24Z by an external live promote** (v0.31.33 committed at 22:24:30Z — the promote's launcher sweep took out the staged daemon). No artifacts survived from that attempt; the verdicts above come from the second (clean) attempt. The fix wave's claims are judged on the .35 run that completed; the external interference is recorded, not blamed on the fix wave.
