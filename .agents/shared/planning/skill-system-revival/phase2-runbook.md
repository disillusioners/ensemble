# Phase-2 switch-flip runbook — agents-ensemble skill-system-revival (PM ENSEMBLE-27 / board `wi-4d1fa093`)

**Lane:** skill-system-revival Phase 1 · branch `feature/skill-system-revival-phase1`
**Worktree (source of every `file:line` below):** `/home/nea/ensemble-src-wt-skill-revival-phase1`
**Live install root (operator host):** `/home/nea/agents-ensemble`
**Companion:** `phase1-plan.md` (§15 is the content spec this document implements) · `decisions.md` (DS-62 is this document's ruling) · `.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md` (the gate)

---

## SCOPE — READ THIS FIRST

**THIS RUNBOOK COVERS THE SWITCH FLIP ONLY.** It assumes the Phase-1 release is **ALREADY LIVE**.

The promote is a **SEPARATE, Emperor-owned 3-factor nonce ceremony**, run from the **scripts-pin worktree**
(`/home/nea/ensemble-src-wt-scripts-pin`) — **not** from this worktree, **not** from the canonical dir. **This document
contains NO promote command, on purpose.** If the release is not live yet, this runbook is not the thing you run; stop and
go to the promote ceremony (`phase1-plan.md` §14.3 documents the command shape; it is executed by the Emperor, not by
this runbook's reader).

**WHAT THIS RUNBOOK DOES NOT DO:** it does not flip any `meta.json` flag, does not declare an A/B winner, does not
hard-delete anything, and does not engage the queue brake until Step 0 of §1 says to.

**HOW TO READ THE CITATIONS.** Every number, command, path, interval and knob in this document carries a `file:line`
citation verified against the code **in this worktree**. Where a fact could NOT be verified from code in this worktree, it
is marked **OPEN ITEM: …** together with what would settle it. There are no estimates and no invented commands.

---

## THE 30-SECOND VERSION (read this if it is 3am)

| | |
|---|---|
| **Kill the lane NOW** | pause the queue `bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2` (`system_parallel_queue`) — takes effect **within one poll cycle (≤ 30 s)** plus the in-flight job's own completion. Command in §3. |
| **Turn the lane ON** | edit the live `.env` and **restart the daemon** — the `.env` is read once at launcher start. ON is **NOT** instant. Commands in §2 and §5. |
| **The order** | **capture first**; evolution only after the capture window is clean. §1. |
| **Never** | declare a winner for the 27 stranded A/B pairs. §9. |
| **If you only remember one thing** | the brake is **broad** — it stops **all** parallel-queue traffic for the project, not just skill jobs. §3. |

---

## 1. THE ORDER — capture FIRST, evolution only after capture proves clean

**The two lanes come up SEPARATELY, in this order, with a full observation window between them:**

```
Step 0  arm + verify the brake                        (§3)
Step 1  flip CAPTURE ONLY  → restart daemon → observe ≥ 24 h   (§1.2, §2, §5, §6)
Step 2  (only if Step 1 is clean) flip EVOLUTION → restart daemon → observe ≥ 24 h
```

The plan's own ordering (§15 step 2): *"Capture first, evolution only after capture proves clean."*

### 1.1 What "capture proves clean" means — MEASURABLE, NOT "IT LOOKS FINE"

**Definition.** The capture window is clean iff, over a **minimum observation period of 24 h (86 400 s)** with the capture
switch ON and the evolution switch still OFF, **ALL TEN** conditions below hold. Each has an exact read (§6 has the
commands; the table names the instrument).

> **Why 24 h and not "a few hours":** 24 h is the house's convergence floor — `CONVERGENCE_MIN_ELAPSED = timedelta(hours=24)`
> (`scripts/audit/skill_counter_reconcile.py:102`), and it is the plan's stated Phase-2 observation window
> (`phase1-plan.md` §15 step 2 and §8 D4, which sets the sink retention at ≥ 30 days to cover it).

| # | Condition (must ALL hold) | Threshold | Instrument |
|---|---|---|---|
| **C1** | The capture lane **actually admitted work** | `counters.admissions` **increases by ≥ 1** over the window | `/api/metrics/capture-lane` |
| **C2** | The capture switch **never flapped off** | `counters.admission_refusals["capture_lane_off"]` **does not increase** | same |
| **C3** | **No keeper deviation** | `counters.deviations` **unchanged** (no increase) | same |
| **C4** | **No Tier-3 mutation happened** (the evolution lane is still OFF) | `counters.fix_admissions` **unchanged** | same |
| **C5** | **No queued evolution row was refused at execution** | `counters.execution_refusals["lane_disabled"]` **unchanged** | same |
| **C6** | **No burst-cap trip** | `counters.admission_refusals["fix_burst_cap"]` **unchanged** | same |
| **C7** | The **A4 within-identity-key divergence gauge did not grow** | `divergence.diverging_keys` **≤ 32** (the production baseline) | `scripts/audit/skill_counter_reconcile.py --measure` |
| **C8** | The **A4 counter drift did not grow** | `drift` **≤ the value at window start** | same |
| **C9** | The **sink** recorded admitted runs | ≥ 1 row with `"outcome":"admitted"` | `<data_dir>/skill-capture-observability/skill-capture-<YYYYMMDD>.jsonl` |
| **C10** | **No new lane anomaly row** | **0** new rows with `category` in (`tool-failure`, `queue-scheduling`) **AND `instance_id = 'daemon:skill-capture-observability'`** in the window | `anomaly_reports` |

**C1's depth: the capture lane must produce a real CAPTURED skill row, not just a job row.** The capture path writes a
`skills` row with `lineage_origin='captured'`
(`daemon/services/skill_evolution_service.py:1028`, the `_evolve_captured` writer at `:732`) **and** a `skill_capture`
job row. **C1 is only satisfied when the `skills` row exists** — a job row alone proves the admission, not the
distillation. That first live CAPTURED row is the Phase-2 proof the plan asks for (`phase1-plan.md` §15 step 8, §9 E2).

> ⚠ **The capture→evolution diversity gap is NOT closed by this.** The CAPTURED-origin path is **REFUSED by design**:
> `SkillEvolutionService.evolve_skill` raises `ValueError` for CAPTURED-origin skills (`phase1-plan.md` §9 E2,
> acceptance #2 — the refusal is *pinned*, i.e. protected). Phase 1 makes the path **reachable, not exercised**, and the
> statement *"the capture→evolution diversity remains UNEXERCISED; Phase 1 makes it reachable, not exercised"* is
> binding on the release notes, the plan, the decision log **and this runbook** (`phase1-plan.md` §9 E2).

**C10's scope is the LANE's rows, not the categories alone** (Phase-3 correction): every lane anomaly row is filed by the
daemon-side reporter `daemon:skill-capture-observability` (`daemon/services/skill_capture_observability.py:139`,
`DAEMON_REPORTER_INSTANCE_ID`), so **every C10 read must carry `AND instance_id = 'daemon:skill-capture-observability'`** —
without it, unrelated lanes' `tool-failure` / `queue-scheduling` rows are read as skill-capture deviations (the scope noise).

**C10's two categories are the lane's own vocabulary, not a guess:** `CATEGORY_LANE_DEVIATION = "tool-failure"` and
`CATEGORY_BURST_CAP = "queue-scheduling"` (`daemon/services/skill_capture_observability.py:146-147`).

**Why C2/C3/C4/C5/C6 say "unchanged" and not "== 0":** all of these counters are **process-local** and **cumulative since
the last daemon start** (`daemon/services/skill_capture_observability.py:241` — a single module-global `_COUNTERS`
object; `counters_snapshot()` at `:249-264` returns a **copy**). A restart resets them to zero
(`reset_counters()` at `:279-285`). Therefore: **take the reading immediately AFTER the restart as the window baseline**,
and compare every later reading against that baseline, never against zero.

### 1.2 The evolution flip (Step 2) — what "clean" must additionally show

Step 2 may begin only when **C1–C10 all held for the full 24 h**, AND:

| # | Condition | Threshold | Instrument |
|---|---|---|---|
| **E1** | The evolution lane **admits** work | `counters.admissions` increases, `counters.fix_admissions` increases | `/api/metrics/capture-lane` |
| **E2** | **No burst cap trip** | `admission_refusals["fix_burst_cap"]` **does not increase** | same |
| **E3** | **No deviation** | `deviations` unchanged | same |
| **E4** | The **A4 drift does not grow** | `drift` ≤ window-start value | `skill_counter_reconcile.py --measure` |
| **E5** | The **A4 divergence gauge does not grow** | `diverging_keys` ≤ 32 | same |

**STOP if E2 trips.** A burst-cap trip during the evolution window means the lane is admitting too much for one skill —
that is exactly the Oct-21 incident shape (`phase1-plan.md` §9 E1: skill `20f916b3` re-FIXed 3× in 37.1 min). The cap
**is the guard working**; do not raise the cap to make it stop.

### 1.3 The A/B backlog precondition (Step 2 only)

**The 27 unresolved A/B groups are a Phase-2 PRECONDITION for the evolution flip** (`phase1-plan.md` §7 item 7,
`decisions.md` DS-34) — but **the mechanism REFUSES to resolve them without a winner decision**, and **no winner may be
declared by this runbook** (§9). So the precondition is **satisfied by the refusal being intact**, not by the backlog
being cleared. Concretely, before Step 2 you must be able to show:

* `skill_ab_tests.resolved_at IS NULL` still holds for the live unresolved groups, **and**
* the deactivation receipt still enumerates them as **refused**, **and**
* the A4 gauge is still **32** — a **drop below 32 would mean a winner WAS declared**, which is a STOP condition (§5, §9).

**STOP if the divergence gauge has dropped below 32.** That is not convergence; that is an undeclared winner.

---

## 2. THE ASYMMETRY — ON needs a restart, OFF is instant

> **⚡ THE ONE THING TO GET RIGHT: turning a lane ON is NOT instant. Turning it OFF IS.**
> You get the **OFF for free** (a queue pause, effective within one poll cycle). You must **PAY for the ON** with a
> daemon restart, because the `.env` is parsed **once**, at launcher start.

### 2.1 Why ON is not instant

| Step | Fact | Citation |
|---|---|---|
| The switch resolver reads `os.environ` **per call** | `_resolve_switch()` does `os.environ.get(env_name)` on every resolution | `daemon/services/skill_job_dispatcher.py:107-121` |
| But the **`.env` file** is parsed **once per launcher process** | `load_env_file "$INSTALL_DIR/.env"` is called in `main()`, **before** the run loop; an in-loop child restart **re-inherits the exports without re-reading `.env`** | `launcher.sh:3351` (call), `launcher.sh:194-237` (the parser, per-key `export`) · `docs/runbooks/systemd-adoption.md:194-199` |
| The systemd unit carries **NO** `.env`-derived `Environment=` lines | the unit's env rung is **EMPTY BY DESIGN**; precedence is `.env` (launcher) → unit `Environment=` (empty) → `systemctl set-environment` (vacuous) → code defaults | `docs/runbooks/systemd-adoption.md:183-192` |

⇒ **Editing the live `.env` changes nothing until the daemon (launcher) is restarted.**

> **⚡ VERIFY THE RESTART — DO NOT ASSUME IT (Phase-3 correction, from the 2026-11-10 flip).** The restart is the ONE step
> whose success is invisible from the `.env`. On 2026-11-10 the capture flip was observed as "flags ON, ZERO admissions"
> while the running process still had the lane OFF — the lane's own sink rows said so
> (`refusal_reason: "capture_lane_off"`, `prompt_source: "skill_metrics_service"`), and the flag-carrying restart landed
> **at 19:37:11**, i.e. AFTER the observation window had closed (the journal shows only two boots that day: 18:12:14 and
> 19:37:12). **⇒ Read the RUNNING PROCESS's environment, not the file:**
>
> ```bash
> pid=$(pgrep -f "/home/nea/agents-ensemble/current/ensemble-prod" | head -1)
> tr '\0' '\n' < /proc/$pid/environ | grep -E '^ENSEMBLE_SKILL_(CAPTURE|EVOLUTION)_ENABLED='
> ```
>
> **Expected:** BOTH lines present with `=1`. **If a line is missing, the flip has NOT landed** — restart again and re-read.
> The sink row's `refusal_reason` is the second tell: `capture_lane_off` means the process still had the lane OFF.

**The same is true of the agent roster.** `skill_injection` is read from `agents/<name>/meta.json` into a **process-lifetime
snapshot**: `get_registry()` builds the global registry **once per process** and calls `discover()`
(`daemon/registry.py:1525-1545`), which parses `meta.json` (`daemon/registry.py:627-637`) and freezes
`skill_injection=meta.get("skill_injection", False)` (`daemon/registry.py:690`, `:756`). ⇒ **Flipping an agent's
`skill_injection` also requires a daemon restart.** Phase 1 flipped **no** agent; the staged roster payload is **armed
only when the capture lane is ON** (`daemon/services/skill_roster.py:385-395` — `stage_payload(..., capture_lane_on=)`;
when the lane is off the payload is empty and `armed=False` with `reason="capture_lane_off"`).

### 2.2 The exact knobs (both lanes)

| Lane | Env var | Default when unset | Accepted ON tokens | Accepted OFF tokens | Invalid value |
|---|---|---|---|---|---|
| **CAPTURE** | `ENSEMBLE_SKILL_CAPTURE_ENABLED` | **OFF** | `1`, `true`, `yes`, `on` (case-insensitive) | `0`, `false`, `no`, `off` | **raises `ValueError`** at first use (fail-closed) |
| **EVOLUTION** | `ENSEMBLE_SKILL_EVOLUTION_ENABLED` | **OFF** | same | same | same |
| **E4 stale deactivation** | `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` | **OFF** | same | same | same |

Citations: capture — `daemon/services/skill_job_dispatcher.py:75` (the name), `:153-176` (`_resolve_capture_enabled`),
`:107-121` (token sets + the `ValueError`); evolution — `daemon/config.py:5226-5233` (the canonical switch wins at every
resolution), `:5243-5261` (the `evolution_lane_enabled` field, default `False`); E4 — `daemon/services/skill_deactivation.py:127-129`,
`:132-149`.

> **⚠ TWO DISCLOSED SURFACES FOR THE EVOLUTION SWITCH.**
> (1) **The alias env var `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED`** — pydantic's `env_prefix="SKILL_EVOLUTION_"`
> (`daemon/config.py:5148`) also exposes the `evolution_lane_enabled` field. It sets the **constructed default only** and
> is **inert whenever the canonical var is set** — but **setting the alias var ALONE DOES ENABLE THE LANE** (disclosed
> residual, `daemon/config.py:5235-5241`). **Set the canonical var only.**
> (2) **An invalid value raises at first use, not at boot** — but it is logged at ERROR at process start
> (`_log_invalid_switch_env_at_load`, `daemon/services/skill_job_dispatcher.py:124-151`). **Check the daemon log for
> `Invalid ENSEMBLE_SKILL_…` after every restart.**

### 2.3 Which side you get for free — said plainly

* **OFF is free and instant.** The queue brake is read fresh from the DB **every poll cycle**
  (`daemon/services/job_processor.py:957`), so pausing the queue needs **no restart** (§3). The switch itself is also
  read per call, so setting `ENSEMBLE_SKILL_CAPTURE_ENABLED=0` in the environment of a **running** daemon would take
  effect immediately — **but there is no supported way to mutate a running daemon's `os.environ`**, so in practice the
  supported OFF lever is the **queue brake**, and the switch OFF is the **belt** for the next restart.
* **ON is not free.** `.env` → **restart** → `os.environ` → per-call resolver. Budget a restart, a `/livez` + `/readyz`
  gate, and a counter-baseline re-read (§1.1).

---

## 3. THE KILL-SWITCH (the queue brake) — **PRIMARY LEVER**

### 3.1 The two identifiers (state both, so the next operator does not repeat the false alarm)

| | |
|---|---|
| **Queue id (the PK)** | `bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2` |
| **Queue name** | `system_parallel_queue` |
| **The name does NOT contain the word "skill".** | A `name ILIKE '%skill%'` search finds **nothing**. This is a filter artifact, not a missing row (`phase1-plan.md` §7 "Census resolution"; `decisions.md` DS-41). |
| **The PK is `queue_id`, NOT `id`.** | `daemon/repositories/job_queue/models.py:201-204` (`queue_id` primary key), `:208` (`queue_name`), `:215` (`is_paused`). |
| **The column is `queue_name`, NOT `name`.** | `daemon/repositories/job_queue/models.py:208`. |

`system_parallel_queue` is additionally a **reserved system-queue name and a valid alias** for the queue tools
(`daemon/services/queue_ref.py:60-72` — `QUEUE_ALIAS_TO_CANONICAL`; `daemon/tools/job_queue.py:331` — the tool's own
argument doc). Alias resolution is **project-scoped** (`resolve_queue_ref`, `daemon/services/queue_ref.py:86-130`).

### 3.2 THE COMMAND — two supported invocations, same effect

**Verify the brake is armed and reads back (read-only, do this FIRST):**

```bash
cd /home/nea/ensemble-src-wt-skill-revival-phase1
export INSTALL_DIR=/home/nea/agents-ensemble
export PGHOST=$(grep -m1 '^POSTGRES_HOST=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGPORT=$(grep -m1 '^POSTGRES_PORT=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGDATABASE=$(grep -m1 '^POSTGRES_DB=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGUSER=$(grep -m1 '^POSTGRES_USER=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGPASSWORD=$(grep -m1 '^POSTGRES_PASSWORD=' "$INSTALL_DIR/.env" | cut -d= -f2-)

psql -Atc "SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';"
```

**Expected:** one row, `system_parallel_queue | f | <iso ts>`. **`STOP if:** the query returns **0 rows** (the queue id is
wrong for this DB — do NOT guess a new one; re-derive it with the `projects`/`job_queues` join in §11 step P1) **or**
`is_paused` is already `t` (the brake is already armed — skip to §3.4).

**ARM the brake — invocation A (the agent tool; the tool-level lever):**

```
queue_update(
    queue_id   = "bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2",   # or the alias "system_parallel_queue"
    project_id = "<project_id of the 'agents-ensemble' project>",   # REQUIRED — ownership validation
    is_paused  = true
)
```

* The tool is `queue_update`, category **`job`** (`daemon/tools/job_queue.py:2360-2367`); the `is_paused` field is
  declared at `:2365` and written at `:2393-2394` (`updates["is_paused"] = is_paused`).
* `project_id` is **required** for ownership validation (`daemon/tools/job_queue.py:2362`; the doc at `:332`).
  The project id is **not** the project name — `projects.project_id` is the PK and `projects.name` is UNIQUE
  (`phase1-plan.md` §7 C2). Get it with:

  ```bash
  psql -Atc "SELECT project_id, name, job_queue_paused FROM projects WHERE name='agents-ensemble';"
  ```
  **Expected:** one row; `job_queue_paused` should be `f` (the **project-level** brake is a SECOND, independent brake —
  `daemon/services/job_queue_service.py:4516`, `:5468`).
* **Expected tool output:** a confirmation string naming the queue.

**ARM the brake — invocation B (the REST route; same service, same write):**

```bash
curl -sS -X PATCH \
  "http://127.0.0.1:9797/api/projects/<project_id>/queues/bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2" \
  -H 'Content-Type: application/json' \
  -d '{"is_paused": true}'
```

* Route: `PATCH /api/projects/{project_id}/queues/{queue_id}` — `daemon/routers/queues.py:324-397` (the `is_paused`
  branch at `:365-366`), router prefix at `:37`, mounted at `/api/queues` (`daemon/api.py:5544`).
* **Expected:** HTTP **200** with the queue JSON, `is_paused: true`.
* **STOP if:** HTTP **404** (queue id or project id wrong — the route returns 404 when the queue is "not found or not
  owned by project", `daemon/routers/queues.py:379-382`); **503** `WRITE_PAUSED` (the daemon is in a write-paused
  migration state — `daemon/routers/queues.py:355`); **401/403** (auth is on and the caller lacks the right scope —
  see the auth note in §6).

**Both invocations land on the same write path:**
`queue_update` / `update_queue` route → `JobQueueMgmtService.update_queue(project_id, queue_id, **updates)`
(`daemon/services/job_queue_mgmt_service.py:366-418`) → `JobQueueRepository.update(queue_id, **updates)`
(`daemon/repositories/job_queue/queue_repository.py:239-273`), which sets the field and **always** refreshes
`updated_at` (`:270`).

### 3.3 THE EXPECTED LATENCY — from the code, not an estimate

**The chain, verified end to end:**

| # | Fact | Citation |
|---|---|---|
| 1 | The job-queue poll interval is **30.0 s** (`JobProcessor(poll_interval: float = 30.0)`), stored as `self._poll_interval` | `daemon/services/job_processor.py:119`, `:138` |
| 2 | Each poll iteration **re-reads the queue rows** and skips a paused one: `if queue.is_paused: continue` | `daemon/services/job_processor.py:957` (inside the per-iteration `for queue in queues_with_work:` at `:956`) |
| 3 | The project-level brake is a **second line of defence**, re-checked inside `JobQueueService.start_job` | `daemon/services/job_queue_service.py:4516`, `:5468` |
| 4 | A job **already admitted** (ACTIVE) is **not** killed by the brake — it runs to its own completion | `daemon/services/job_processor.py:957` skips *admission*; the ACTIVE row's own completion path is untouched |

⇒ **"The capture lane stops within one poll cycle (≤ 30 s), plus the in-flight job's own completion."**

**Precisely:**
* a job **not yet admitted** does not start for **up to one poll cycle (≤ 30 s)** after the row is committed;
* a job **already admitted** runs to completion — **unbounded** by the brake;
* the project-level brake (`projects.job_queue_paused`) is an independent second lever.

**STOP if:** after arming, the read-back in §3.2 does not show `is_paused = t` — the brake is NOT armed and everything
after this point is unsafe.

### 3.4 What the brake actually costs — **it is BROAD, not skill-specific**

The queue carries **25 `skill_capture` + 114 `skill_evolution`** rows and **all other parallel-queue traffic for the
project** (`phase1-plan.md` §7 C6). **Pausing it stops everything on that queue, not just the skill lanes.** Say this out
loud before you pull it at 3am. The blast radius is the price of the instant lever.

### 3.5 `job_pause` is **NOT** the brake

The tool `job_pause` exists at `daemon/tools/job_queue.py:3899-3902`, and it is **a different lever**: it *"Pause[s] the
instance behind an ACTIVE job and cascade[s] to its lineage"* (`:805`, `:3899`) — **the job row's state is NOT touched**
(`:3895-3897`: *"Job state (admission_state / status) is NOT touched by these tools — pause/resume operates on the
instance lineage, NOT on the job row"*). **It does not pause a queue and does not stop admissions.** Use `queue_update`
with `is_paused`.

---

## 4. THE SECOND LEVER — `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` (default 0, inert)

### 4.1 What it arms

It arms **E4.1 — the boot-time stale/shadow skill reconciliation**: an idempotent pass that applies the rules
`R1` / `R2` / `R3` / `R5`, **deactivates** matching rows (`is_active=False, status='inactive'` — the sanctioned
dual-column writer `SkillRepository.deactivate`, `daemon/repositories/skill/repository.py:383-396`), **never
hard-deletes**, **never re-activates**, and writes an **audit receipt**
(`daemon/services/skill_deactivation.py:120-165` — the rule constants at `:124-126`, the env name at `:127-129`, the
resolver at `:132-149`; the boot call at `daemon/manager.py:2786` → `_run_skill_stale_deactivation_reconciliation` at
`daemon/manager.py:5768-5800`).

### 4.2 What OFF means — **NOTHING is built, written or enumerated**

When the flag is off (the default; **absent = off**):

* **no receipt** is written;
* **no row** is changed;
* **no refusal enumeration** happens;
* **`SkillStaleDeactivator` is not even constructed** — the boot step returns *before* the import-and-construct block.

Citations: `daemon/services/skill_deactivation.py:143-148` (the inert-when-off contract, verbatim) and
`daemon/manager.py:5787-5797` (the brake check and the early `return` that precedes the deactivator construction).

### 4.3 WHEN to arm it — **TOGETHER WITH the capture switch, NEVER BEFORE**

> **Rule: arm `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED=1` in the SAME `.env` edit that arms
> `ENSEMBLE_SKILL_CAPTURE_ENABLED=1`, and restart ONCE. Never arm it before the capture switch.**

**Why (the recorded reasoning):** shipping the pass ungated means it runs on LIVE's next boot at promote — writing a
receipt into live's data dir and normalizing rows in the live `skills` table **with nobody having armed anything**. A
boot-time write to production data absent explicit enablement violates the lane's core discipline, even though it
deactivates nothing (`decisions.md` DS-54). The lever was created **specifically** to be a named Phase-2 lever armed
**alongside** the capture switch.

### 4.4 The one thing it will NOT do

**It will not resolve the 27 stranded A/B pairs, and it will not declare a winner.** A row whose A/B test is unresolved
is **REFUSED** and enumerated as `skipped_open_ab_test`; the pair-resolver refuses with
`skipped_no_winner_decision` (`daemon/services/skill_deactivation.py:151-154`). **That refusal is a feature** (§9).

---

## 5. GO / NO-GO CRITERIA PER FLIP

### 5.1 GO for the CAPTURE flip (Step 1)

| # | Criterion (checkable) | Exact read |
|---|---|---|
| **G1** | The Phase-1 release **is live** | `readlink /home/nea/agents-ensemble/current` → the release dir; and `python3 -c "import json;print(json.load(open('/home/nea/agents-ensemble/releases/state.json'))['current'])"`. **STOP if** it is not the Phase-1 release. |
| **G2** | The **brake is armed and verified** | §3.2 read-back shows `is_paused = t` |
| **G3** | The capture switch is **currently OFF** | `grep -c '^ENSEMBLE_SKILL_CAPTURE_ENABLED=' /home/nea/agents-ensemble/.env` → `0` (absent) or the value is an OFF token |
| **G4** | The evolution switch is **currently OFF** | same for `ENSEMBLE_SKILL_EVOLUTION_ENABLED` — **it must still be OFF when capture comes up** |
| **G5** | The A4 divergence gauge is **at the expected baseline** | `skill_counter_reconcile.py --measure` → `divergence: 32 divergent (name, project_id) keys …` (production baseline **32**; dev is 0). **STOP if** it is not 32 — a different number means something changed (§9). |
| **G6** | The A4 counter drift **has not grown since the last reading** | `--measure` → `drift:`; compare with the stored value |
| **G7** | **The metrics door answers** | `GET /api/metrics/capture-lane` → **200** with a JSON body. **STOP if** it is **503 `AUTH_NOT_CONFIGURED`** (auth is OFF ⇒ the door is fail-closed) — you then have no counter read, so **do not flip**. |
| **G8** | The **live-PG leg is green on this host** | §8 step 8.3 — the pack must not print `the live-PG leg is NOT VERIFIED on this host` |
| **G9** | The **deactivation receipt** is where the operator expects it | §8 step 8.5 |
| **G10** | The **brake state is known and written down** | the §3.2 read-back, timestamped, in the audit log |

### 5.2 NO-GO — the metrics-honesty criteria (do not improvise past these)

**(a) The A4 gauge — within-identity-key divergence. Production baseline = 32, dev = 0.**

* The gauge is `SkillRepository.within_identity_key_divergence()` (`daemon/repositories/skill/repository.py:803-847`),
  measured as `count(DISTINCT content) > 1` grouped by `(name, project_id)` — a pure-ORM `GROUP BY` + `func.count`
  (`:876-889`), dialect-safe.
* Its own docstring states the baseline: **32 on production (630 rows), 0 on dev (15 rows)** (`:843-846`).
* **THE 27-stranded-A/B-pair cause, named:** the 27 stranded pairs are 27 multi-active `(name, project_id)` families
  (54 active rows, 26 distinct names) — but **2 of them carry IDENTICAL content**, so the multi-active backlog can
  explain **at most 25** of the 32 divergent keys. The **residual 7** keys are NOT produced by a multi-active family:
  the gauge's population is **ALL** rows, so a key whose single active row coexists with a differing INACTIVE generation
  also diverges (`:848-861`).
* **THE 0 TARGET IS EXPLICITLY BLOCKED-ON THE E-6 DECISION** (`:863-865`, verbatim: *"The **0 target is BLOCKED-ON the
  A/B resolution decision** (Ruling E) — do NOT read this gauge's non-zero value as a Phase-1 regression"*). E-6 is the
  Emperor's decision on **which variant wins** in each of the 27 pairs (`phase1-plan.md` §16.3 E-6; `decisions.md`
  DS-44). **Nobody in this lane, in this runbook, may move this gauge to 0.**
* **⇒ GO/NO-GO:** the gauge must read **exactly 32** at the pre-flight. A value **> 32** is a drift — investigate.
  A value **< 32** is a **winner declared somewhere** — **STOP**.

**(b) The A3 predicate — the CANDIDATE band (the gate) vs the BLOCKER band (the monitor) — ROUND-2 F1 BAND MAP.**
`has_genuinely_evolved_for_instance` (`daemon/repositories/skill/repository.py:1699`) is the **three-conjunct** predicate
`selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL`. Derived from the Gate-4 code
(`daemon/services/skill_metrics_service.py:733-772`): those predicate rows are the genuinely-evolved **EVIDENCE** — when an
instance HAS such a row, `:765-769` REFUSES capture (`return None`). The two bands therefore map as:
* **CANDIDATE band = `feedback_applied IS NULL`** — **THE GATE (§8.1(a))**: selected+succeeded rows with no feedback
  evidence ⇒ the instance is NOT genuinely evolved ⇒ capture MAY still fire (`:765` falls through). This is the only
  PREDICTIVE population: it **must be > 0**, and **0 is the STOP** (nothing left to capture ⇒ the lane can never admit).
* **BLOCKER band = `feedback_applied IS NOT NULL`** — **THE MONITOR (§8.1(b))**: capture is BLOCKED on those instances
  (evidence present ⇒ CANNOT capture), so a non-zero count is NORMAL and EXPECTED — **never a gate, NEVER a STOP**.
Live census (2026-11-10T21:52Z, read-only): candidate **3 544** rows / 3 525 instances; blocker **5 996** rows / 5 944
instances (candidate **3 457** rows on the 2026-11-09 census). *(Round-2 correction history: the round-1 text mapped the
GATE onto the blocker band with "STOP if 0" — trivially satisfied, so the gate passed by accident — and demoted the
candidate band to a monitor. Both are inverted above; band map quoted from code in the round-2 lane report.)*
See §8 step 8.1 for the exact reads.

**(c) The capture-lane-off counter is COMPLETE — three sites, one key.**
`admission_refusals["capture_lane_off"]` is incremented **before** the refusal at **all three** production refusal
sites — the metrics seam (`daemon/services/skill_metrics_service.py:663` guard → `:682` recorder → `:687` refusal), the
tools seam (`daemon/tools/skill_evolution_tools.py:522` guard → `:539` recorder → `:563` refusal), and the dispatcher
gate (`daemon/services/skill_job_dispatcher.py:1126` guard → `:1136` `record_admission_refusal("capture_lane_off")` →
`:1148` refusal). All three move the **same** key (`daemon/services/skill_capture_observability.py:291-292`, `:355`).
The 4th read site (`daemon/services/skill_roster.py:382`) only **reports** the switch.
⇒ **A non-zero `capture_lane_off` count is a HARD signal that the lane was refused** — it is never partial.

### 5.3 GO for the EVOLUTION flip (Step 2)

All of §5.1's criteria **re-verified fresh** (the brake may still be armed, but the capture switch is now ON and the
evolution switch is still OFF), **plus** §1.2's E1–E5, **plus**:

| # | Criterion | Exact read |
|---|---|---|
| **G11** | The A4 gauge is **still 32** | `--measure` → `diverging_keys = 32`. **STOP if < 32.** |
| **G12** | The divergence gauge did not **grow** during the capture window | compare with the window-start reading |
| **G13** | The capture sink shows **admitted** runs and **no** `deviation: true` | `<data_dir>/skill-capture-observability/skill-capture-<YYYYMMDD>.jsonl` |
| **G14** | `anomaly_reports` has **no** new lane rows | the §6.4 SELECT (lane-scoped: `instance_id = 'daemon:skill-capture-observability'`) |

---

## 6. MONITORING CHECKS — BEFORE AND AFTER EACH FLIP

### 6.1 The four metrics reads (exact routes, exact auth)

All four live on the **same** router, mounted at **`/api`** (`daemon/api.py:5342` prefix; `daemon/api.py:5577` include;
router prefix `/metrics` at `daemon/routers/metrics.py:94`).

| Read | Route | Handler | Source |
|---|---|---|---|
| **capture-lane counters** | `GET /api/metrics/capture-lane` | `get_capture_lane_counters` | `daemon/routers/metrics.py:836-837` (route), `:857` (handler) |
| metric families | `GET /api/metrics/families` | `get_metrics_families` | `daemon/routers/metrics.py:581` (route), `:601` (handler) |
| published values | `GET /api/metrics/snapshot` | `get_metrics_snapshot` | `daemon/routers/metrics.py:630` (route), `:652` (handler) |
| trend series | `GET /api/metrics/series` | `get_metrics_series` | `daemon/routers/metrics.py:703` (route), `:724` (handler) |

**Auth / scope — the SAME for all four** (`daemon/routers/metrics.py:581-599`, `:630-649`, `:703-722`, `:836-856`):

| Condition | Result |
|---|---|
| Caller has **`maintenance:read`** OR **`metrics:read`** | **200** |
| Auth ON, caller unauthenticated | **401** |
| Caller lacks both scopes | **403** |
| **Auth OFF** (`app.state.auth_config` absent) | **503 `AUTH_NOT_CONFIGURED`** — the fail-closed gate fires **before** the counters are read (`daemon/routers/metrics.py:136-152`, `:886-888`) |

**Expected body of `/api/metrics/capture-lane`** (verbatim shape, `daemon/routers/metrics.py:862-874`):

```json
{
  "counters": {
    "admission_refusals": {"capture_lane_off": 0, "fix_burst_cap": 0},
    "execution_refusals": {"lane_disabled": 0},
    "admissions": 0,
    "deviations": 0,
    "fix_admissions": 0
  },
  "source": "daemon.services.skill_capture_observability",
  "exposition_neutral": true
}
```

**The snapshot is a COPY** (`counters_snapshot()`, `daemon/services/skill_capture_observability.py:249-264`) — a caller
cannot mutate the daemon's counters through it.

> **STOP if the capture-lane read returns 503.** That means auth is OFF and the door is fail-closed. You have **no
> counter read**, so you cannot run §1.1's conditions. Either turn auth on (a separate, out-of-scope change) or do not
> flip.

### 6.2 The capture-lane-off counter — **COMPLETE at three sites** (read it, don't reason about it)

| Site | Guard | Recorder (fires **before** the refusal) | Refusal |
|---|---|---|---|
| metrics seam | `daemon/services/skill_metrics_service.py:663` | `:682` (`_record_capture_lane_off_refusal`, defined at `:140`) | `return None` at `:687` |
| tools seam | `daemon/tools/skill_evolution_tools.py:522` | `:539` (`observability.record_capture_lane_disabled_refusal(...)`) | envelope returned at `:563` |
| dispatcher gate | `daemon/services/skill_job_dispatcher.py:1126` | `:1136` (`record_admission_refusal("capture_lane_off")`) + the sink record | `return None` at `:1148` |

All three move **ONE** key: `admission_refusals["capture_lane_off"]`
(`daemon/services/skill_capture_observability.py:288-293`, the key documented at `:325`, `:354`).

### 6.3 The execution-refusal counter and its terminal row

**These are a DIFFERENT signal from the admission refusal and must never be conflated** (`decisions.md` DS-19;
`phase1-plan.md` §8 D1).

* Counter: `execution_refusals["lane_disabled"]` — `daemon/services/skill_capture_observability.py:296-301`.
* Terminal row: `job_queue_items.terminal_reason = 'lane_disabled'` — written by the guarded queued→done transition
  (`daemon/services/job_processor.py:788`), with the counter incremented at `:818` and the sink record at `:819-828`;
  the ACTIVE-row fallback path increments at `:867`.

```bash
psql -Atc "SELECT count(*) FROM job_queue_items WHERE terminal_reason = 'lane_disabled';"
psql -Atc "SELECT job_id, job_type, queue_id, created_at FROM job_queue_items WHERE terminal_reason='lane_disabled' ORDER BY created_at DESC LIMIT 20;"
```

> **⚠ SCOPE FACT — read this before you panic.** The execution refusal gates the **`skill_evolution`** lane
> (`daemon/services/job_processor.py:1092-1096`, `:1437-1453`, the scope comment *"strictly `job_type ==
> 'skill_evolution'` rows"* at `:1437`). **There is NO capture-off gate at execution time** — a `skill_capture` row
> admitted while the lane was ON still executes after a flip-off. So during the **capture-only** window,
> `execution_refusals["lane_disabled"]` must be **unchanged** (C5) — it is the **evolution** lane's signal, not the
> capture lane's.

### 6.4 The D3 deviation trigger — the anomaly row

The keeper deviation is announced **by the daemon**, not hoped for by the agent
(`daemon/services/skill_capture_observability.py:754-812` — `emit_lane_anomaly`, which resolves the **real** anomaly
service and calls its accept path; `:796-806` the accept call; `:795-800` the "no reporter available ⇒ deviation NOT
announced" WARNING).

* Category: **`tool-failure`** (`daemon/services/skill_capture_observability.py:146`).
* Severity: **`major`**.
* Emit site: `daemon/services/skill_evolution_service.py:1419-1434` — the *forced A/B resolve refused below sample
  size* refusal (the Oct-21 incident shape).
* **The row read** (table `anomaly_reports`, `daemon/repositories/anomaly/models.py:145`):

```bash
psql -Atc "SELECT id, created_at, agent_id, instance_id, category, severity, left(description,160) FROM anomaly_reports WHERE category IN ('tool-failure','queue-scheduling') AND instance_id = 'daemon:skill-capture-observability' ORDER BY created_at DESC LIMIT 20;"
```

* **Expected while healthy:** no new rows in the window. **STOP if:** a `tool-failure` row appears — that is a keeper
  deviation; go to §7 (brake) and §9.
* The counter `deviations` is the fast read; the anomaly row is the durable one.

### 6.5 The FIX-admission gauge — **process-local gauge AND durable DB count, and how they differ**

| | Process-local gauge | Durable DB count |
|---|---|---|
| **What** | `counters.fix_admissions` — incremented by `record_admission(is_fix=True)` (`daemon/services/skill_capture_observability.py:304-308`, called at `daemon/services/skill_job_dispatcher.py:1079`) | `count_fix_admissions_since(skill_id, since_iso)` — a **COUNT over committed `job_queue_items` rows** (`daemon/repositories/job_queue/repository.py:659-723`, the statement builder at `:34-79`) |
| **Scope** | a **cumulative in-process total**, since the last restart; **no** time window | **per skill id**, over an explicit window |
| **Survives a restart?** | **NO** — `_COUNTERS` is module-global (`daemon/services/skill_capture_observability.py:241`) and `reset_counters()` (`:279-285`) clears it | **YES** — it reads committed rows |
| **Counts refusals?** | **NO** — a refused admission writes **no row** (`phase1-plan.md` §8 D1) and the gauge only moves on admission | **NO** — refusals write no row either; the cap's counter (`admission_refusals["fix_burst_cap"]`) is the refusal signal |

**⇒ The two numbers are NOT the same quantity and are NOT expected to match.** The gauge is a *rate-of-arrival* signal
for the live window; the DB count is the *durable, per-skill* evidence the burst cap is built on (it is what makes the
cap survive a restart — `phase1-plan.md` §9 E1 acceptance #2).

```bash
# durable, per skill, over a window (the cap's own instrument):
psql -Atc "SELECT count(*) FROM job_queue_items WHERE job_type='skill_evolution' AND metadata->>'evolution_type'='FIX' AND created_at >= now()::text - interval '24 hours';"
```

> **⚠ DIALECT NOTE.** That query is the **PostgreSQL** spelling (`metadata->>'skill_id'` / `->>'evolution_type'`). On
> SQLite the spelling is `json_extract(metadata,'$.skill_id')`
> (`daemon/repositories/job_queue/repository.py:68-79`). The production DB is PostgreSQL.

### 6.6 The burst-cap refusal signal — **anomaly row + counter + WARNING log + sink record + HTTP 429**

When the durable cap refuses the N-th FIX admission for one skill inside the window:

| Signal | Where | Citation |
|---|---|---|
| **Counter** | `admission_refusals["fix_burst_cap"]` | `daemon/services/skill_job_dispatcher.py:793` |
| **WARNING log** | `SkillJobDispatcher.enqueue_evolution: refused: burst cap — N FIX admissions for skill <id> within <w>s (cap <c>). NO skill-keeper job was created.` | `daemon/services/skill_job_dispatcher.py:791-792` (message built at `:781-790`) |
| **Sink record** | `stage="admission"`, `outcome="refused"`, `refusal_reason="fix_burst_cap"`, plus `fix_admissions_in_window`, `cap`, `window_seconds` | `daemon/services/skill_job_dispatcher.py:794-807` |
| **Anomaly row** | `category="queue-scheduling"`, `severity="major"` | `daemon/services/skill_job_dispatcher.py:802-812` (category constant `daemon/services/skill_capture_observability.py:147`) |
| **REST status** | **429 Too Many Requests** — `{"error": <message>, "reason": <e.reason>}` | `daemon/routers/skills.py:1465-1485` (the `except SkillFixBurstCapRefused` branch); the route is `POST /api/skills/{skill_id}/fix` (`daemon/routers/skills.py:1364`) |

**The 429 read:**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  "http://127.0.0.1:9797/api/skills/<skill_id>/fix" \
  -H 'Content-Type: application/json' \
  -d '{"issue":"probe"}'
```
**Expected while healthy:** **202** (admitted) — the route's documented success is *202 Accepted with `{"job_id": str}`*
(`daemon/routers/skills.py:1390-1391`, `:1460-1463`).
**A 429** = the cap refused: that is the **guard working**, and it is a **429, not a 500** (`daemon/routers/skills.py:1466-1479`).
**STOP if you ever see a 500 on this route** — that is the generic-fault path, i.e. a real error, not a designed refusal.

**Cap defaults:** `fix_burst_cap = 3` per `fix_burst_window_seconds = 86400` (24 h)
(`daemon/config.py:5295-5307`, `:5308-5318`). Env aliases: **`SKILL_EVOLUTION_FIX_BURST_CAP`** /
**`SKILL_EVOLUTION_FIX_BURST_WINDOW_SECONDS`** (pydantic `env_prefix="SKILL_EVOLUTION_"`, `daemon/config.py:5148`).
**⚠ `fix_burst_cap = 0` DISABLES the durable cap** (`daemon/config.py:5300-5302`); a **negative** cap is **rejected at
load**, not clamped (`ge=0`, `:5297`; `:5304-5305`). **Do not set 0 to silence a trip** — a trip is information.

### 6.7 The capture sink — the durable, restart-free read side

* Directory: `<data_dir>/skill-capture-observability/` (`SINK_DIR_NAME`, `daemon/services/skill_capture_observability.py:151`).
* File: `skill-capture-<YYYYMMDD>.jsonl` (`_SINK_FILE_PATTERN`, `:157-161`).
* `<data_dir>` resolution: **`ENSEMBLE_DATA_DIR` > `DATA_DIR` > `./data`** (`resolve_data_dir`,
  `daemon/services/skill_capture_observability.py:397-412`). On the live install neither var is set in the `.env` and the
  daemon's CWD is the install root (`launcher.sh:3340-3342`; the unit's `WorkingDirectory=$INSTALL_DIR`,
  `scripts/upgrade/adopt-unit.sh:394`) ⇒ **`<data_dir>` = `/home/nea/agents-ensemble/data`**.
* Retention: **90 days**, pruned by the **NAME-ENCODED DAY**, never the mtime (`SINK_RETENTION_DAYS`, `:155`;
  `_prune_expired_sink_files`, `:472-509`).
* Record shape: `stage` / `run_id` / `agent_id` / `prompt_source` / `prompt_shape` / `outcome` / `refusal_reason` /
  `deviation` (`build_run_record`, `:825-868`) — **never the raw prompt text** (`:838-841`).

```bash
DATA_DIR=/home/nea/agents-ensemble/data
SINK="$DATA_DIR/skill-capture-observability"
ls -la "$SINK"                                  # expected: one file per day
grep -c '"outcome": "admitted"' "$SINK"/skill-capture-$(date -u +%Y%m%d).jsonl 2>/dev/null || echo 0
grep -c '"deviation": true' "$SINK"/skill-capture-$(date -u +%Y%m%d).jsonl 2>/dev/null || echo 0
```

> **⚠ MEASUREMENT HYGIENE — MOVE FILES ASIDE, NEVER `rm`.** The sink is **one shared daily file** — every lane's rows
> land in it (`daemon/services/skill_capture_observability.py:490-491`: *"every lane's rows land in the SAME daily
> file, so a wrong unlink destroys another lane's evidence"*). The gate lost 116 evidence rows to exactly this
> (gate finding **F-G1**, MAJOR, `.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md:147`). The
> per-lane redesign is a **disclosed fast-follow, not built** (`phase1-plan.md` §17 item 37, `:1037`). **The compensating
> control is: to get a clean reading, MOVE the file aside (rename it out of the sink dir) — NEVER delete it.**

### 6.8 BEFORE and AFTER each flip — the checklist shape

**BEFORE (run all of these; record the values — they are the window baseline):**

```bash
# 1. the capture-lane counters (the §1.1 baseline)
curl -sS http://127.0.0.1:9797/api/metrics/capture-lane | tee /tmp/capture-lane.before.json

# 2. the A4 gauge + drift (the §5.1 G5/G6 baseline)
cd /home/nea/ensemble-src-wt-skill-revival-phase1
.venv/bin/python scripts/audit/skill_counter_reconcile.py \
  --db-url "postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}" \
  --measure --json | tee /tmp/a4.before.json

# 3. the anomaly baseline
psql -Atc "SELECT count(*) FROM anomaly_reports WHERE category IN ('tool-failure','queue-scheduling') AND instance_id = 'daemon:skill-capture-observability';"

# 4. the brake state (the §3.2 read-back)
psql -Atc "SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id='bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';"
```

**AFTER the restart (and after each window):** re-run the same four, and diff against the stored baselines. The
**counter baseline resets on restart** — the §1.1 "unchanged" conditions are therefore measured **against the
post-restart reading**, not against the pre-restart one.

---

## 7. ROLLBACK LEVERS — RANKED BY SPEED

| Rank | Lever | Command | Expected recovery | Notes |
|---|---|---|---|---|
| **1 (instant)** | **Queue pause** (the brake) | `queue_update(queue_id="bd075f0b-…", project_id="<pid>", is_paused=true)` — or `PATCH /api/projects/<pid>/queues/bd075f0b-…` with `{"is_paused": true}` (§3.2) | **≤ 30 s** (one poll cycle) + the in-flight job's own completion | **Broad** — stops all parallel-queue traffic for the project (§3.4). No restart needed. |
| **2 (slow)** | **The switch OFF + daemon restart** | set `ENSEMBLE_SKILL_CAPTURE_ENABLED=0` (and/or `ENSEMBLE_SKILL_EVOLUTION_ENABLED=0`) in the live `.env`, then restart the daemon (§2.2) | **one restart cycle** + `/livez` + `/readyz` gate | The **belt** to the brake's braces: it makes the OFF durable across the next boot. `Restart=on-failure` means an un-restarted `.env` edit can apply itself on the next crash without anyone asking — **brake first, switch second** (`phase1-plan.md` §15 step 3). |
| **3 (slowest, pre-promote only)** | **Revert + re-promote the release** | the promote ceremony, from the scripts-pin worktree | a full ceremony | **NOT THIS RUNBOOK's job** (§12). Named here only so the operator knows it exists. |

**ROLLBACK ORDER: brake FIRST, switch SECOND.** Never edit the `.env` first — an un-restarted `.env` edit is a landmine
that arms itself on the next crash.

**After any rollback, re-read the counters and the brake state and record them** (§6.8's four reads) — a rollback without
a read-back is not a rollback.

---

## 8. POST-FLIP VERIFICATION — MEASUREMENT items (not code items)

> **Everything in this section is a MEASUREMENT.** The code already shipped; this section says what to READ to prove it
> behaves, and what the numbers must be.

### 8.1 A3 — the attribution predicate and the 3 457-row window

**The shipped predicate** (`daemon/repositories/skill/repository.py:1699-1719`):

```
selected = TRUE  AND  task_succeeded = TRUE  AND  feedback_applied IS NOT NULL
```

**What the 3 457 is.** The census window `selected AND task_succeeded AND feedback_applied IS NULL` = **3 457 rows** on the
2026-11-09 census. Under the OLD (feedback-absent) predicate this window would have been FALSELY admitted as
"genuinely evolved"; the shipped predicate's `feedback_applied IS NOT NULL` conjunct refuses every one of those
admissions, so the **false-ADMISSION delta is 0 by construction** (`daemon/repositories/skill/repository.py:1717-1719`).
**ROUND-2 F1 clarification (load-bearing):** "excluded by construction" means excluded FROM THE GENUINELY-EVOLVED SET
ONLY — it is NOT exclusion from capture. Precisely because these rows carry no feedback evidence, the instances behind
them are NOT genuinely evolved, so the capture gate MAY still fire on them: **this window IS the capture CANDIDATE band**
(§8.1(a) below, the gate). Live: **3 544 rows / 3 525 instances** (2026-11-10T21:52Z census).

**THE INSTRUMENT LIMIT — stated honestly (this is a leader-list item to note).**
`scripts/audit/skill_counter_reconcile.py` is the **A4** instrument: its `measure_snapshot()`
(`scripts/audit/skill_counter_reconcile.py:197-224`) measures the **counter drift** (`counter_total` vs `ledger_total`,
from `skills.total_applied` and `skill_usage_records where applied is true`, `:136-152`) and **reports the A4 divergence
gauge** (`divergence`, `:212`, delegated to `SkillRepository.within_identity_key_divergence`).
**It does NOT measure A3's 3 457-row population.** There is **no shipped CLI instrument for A3's false-eligible
population** — the figure is a census constant carried in the predicate's docstring and pinned by
`tests/unit/test_skill_capture_attribution_seam.py` (`:38`, `:289-290`). *(That "false-eligible" window IS the
CANDIDATE band — the capture-eligible population, §8.1(a) — so it is measured live by the SELECT below, just not by a
shipped CLI instrument.)*

**⇒ What the operator can read live, derived from the shipped predicate** (read-only; the predicate's own terms, the
`skill_usage_records` table at `daemon/repositories/skill/models.py:368`):

```bash
# (a) THE GATE — the CANDIDATE band (the population the capture lane can STILL fire on):
psql -Atc "SELECT count(*) FROM skill_usage_records WHERE selected AND task_succeeded AND feedback_applied IS NULL;"
```
**Expected: > 0 — live census 3 544 rows / 3 525 instances (2026-11-10T21:52Z; 3 457 on 2026-11-09).**
**STOP if it is 0:** the candidate band is the only PREDICTIVE population — these selected+succeeded rows carry no
feedback evidence, so the instances behind them are NOT genuinely evolved and the capture gate MAY still fire
(`skill_metrics_service.py:765` falls through). An empty candidate band means NOTHING IS LEFT TO CAPTURE ⇒ the lane can
never admit ⇒ **the gate fails**.

```bash
# (b) THE MONITOR — the BLOCKER band (NOT a gate, NEVER a STOP):
psql -Atc "SELECT count(*) FROM skill_usage_records WHERE selected AND task_succeeded AND feedback_applied IS NOT NULL;"
```
**Expected: ≈5 996 on the 2026-11-10T21:52Z live census (5 944 distinct instances) — a NON-ZERO value is NORMAL.** This
band is BLOCKED from capture BY CONSTRUCTION (`feedback_applied IS NOT NULL` is the genuinely-evolved EVIDENCE the
predicate demands — `repository.py:1755`; the Gate-4 code REFUSES capture when it finds it, `skill_metrics_service.py:765-769`),
so a non-zero count here is the SHIPPED PREDICATE WORKING, not a fault. It is a MONITOR (a sudden drop means the usage rows
changed shape), **never a gate**.

> **WHY THIS READ WAS CORRECTED AGAIN (Phase-3 round-2 F1).** The round-1 text put the GATE on (b) — the blocker band —
> with "STOP if 0": a gate that is TRIVIALLY satisfied because the blocker band is ~always non-zero (it read **5 996**
> on 2026-11-10T21:52Z), so the gate passed by accident while the real predictive population was demoted to a monitor.
> The band map is derived from the Gate-4 code (`daemon/services/skill_metrics_service.py:733-772`, quoted in the
> round-2 lane report): `feedback_applied IS NULL` = CANDIDATE = the gate, expected > 0, STOP if 0;
> `feedback_applied IS NOT NULL` = BLOCKER = monitor only. The gate is (a), the monitor is (b).

### 8.2 A4 — the drift convergence, with the ≥ 24 h gate

**The CLI** (`scripts/audit/skill_counter_reconcile.py:371-421`; the parser at `:379-421`):

| Flag | Required? | Meaning | Citation |
|---|---|---|---|
| `--db-url URL` | **REQUIRED** | "the script never guesses a connection, so a bare invocation cannot reach a database by accident" | `:379-387` |
| `--measure` | one of the mutually-exclusive modes | the canonical reading + the A4 divergence gauge, **no writes** | `:394-402` |
| `--apply` | one of the modes | commit the repair (the default is a **dry run**) | `:389-393` |
| `--revert RECEIPT_JSON` | one of the modes | restore the BEFORE values recorded in a receipt | `:402-407` |
| `--compare A B` | its own mode | evaluate convergence between two saved `--measure` readings | `:407-412` |
| `--receipt PATH` | optional | where to write the audit receipt under `--apply` | `:407-412` |
| `--json` | optional | machine-readable output | `:412-421` |

**The ≥ 24 h convergence gate.** `CONVERGENCE_MIN_ELAPSED = timedelta(hours=24)`
(`scripts/audit/skill_counter_reconcile.py:102`). `evaluate_convergence(first, second)` (`:329-363`) requires **BOTH**
readings to show `drift == 0` **AND** the two readings to be **≥ 24 h apart**:

* `status = "converged"` when both drifts are 0 and ≥ 24 h elapsed → **exit 0** (`:487-489`);
* `status = "insufficient_elapsed"` when the readings are closer than 24 h → **exit 3** (`:490-492`), with the reason
  *"the two readings are closer than the 24 h the plan requires; convergence must be re-measured >= 24 h later"*
  (`:350-353`);
* anything else (drift persists) → **exit 1** (`:493`).

**The procedure:**

```bash
cd /home/nea/ensemble-src-wt-skill-revival-phase1
DB_URL="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"

# 1. DRY RUN FIRST — always.
.venv/bin/python scripts/audit/skill_counter_reconcile.py --db-url "$DB_URL" --measure --json | tee /tmp/a4.t0.json

# 2. If a repair is needed, run it EXPLICITLY and keep the receipt.
.venv/bin/python scripts/audit/skill_counter_reconcile.py --db-url "$DB_URL" --apply \
  --receipt /tmp/a4.receipt.json --json | tee /tmp/a4.apply.json

# 3. Re-measure — and do NOT compare yet.
.venv/bin/python scripts/audit/skill_counter_reconcile.py --db-url "$DB_URL" --measure --json | tee /tmp/a4.t1.json

# 4. >= 24 h LATER, measure again and compare.
.venv/bin/python scripts/audit/skill_counter_reconcile.py --db-url "$DB_URL" --measure --json | tee /tmp/a4.t2.json
.venv/bin/python scripts/audit/skill_counter_reconcile.py --db-url "$DB_URL" \
  --compare /tmp/a4.t1.json /tmp/a4.t2.json --json
echo "exit=$?"     # 0 = converged ; 3 = insufficient_elapsed ; 1 = drift persists
```

**Expected:** `exit=0`, `"status": "converged"`, `"elapsed_seconds": ≥ 86400`.
**STOP if:** `exit=3` (too early — wait, do **not** re-run `--apply` to "make it converge"; the window is the point) or
`exit=1` (`drift persists` — the leak is live again; go to §7).

**Reversal:** `--revert /tmp/a4.receipt.json` restores the BEFORE values and re-verifies convergence
(`scripts/audit/skill_counter_reconcile.py:513-527`).

> **RUN THE SCRIPT FROM THE WORKTREE, NOT THE INSTALL.** The release payload contains only `ensemble-prod`, `agents/`,
> `frontend/dist/frontend/browser/`, `plugins/`, `config.yaml`, `launcher.sh` (`scripts/upgrade/stage.sh:395-425`) —
> **there is no `scripts/audit/` in the payload.** Use the worktree's own `.venv/bin/python`
> (`/home/nea/ensemble-src-wt-skill-revival-phase1/.venv/bin/python`, CPython 3.14).

### 8.3 The **bounded** post-promote dual-side check — scoped to `daemon/manager.py` reach

**This is the compensating control for the gate's deliberate scope reduction.** The gate did **not** run a whole-tree
Tier-2 dual-side sweep: the diff touches core seams (`daemon/manager.py`), which per `ensure.md` Gate Tiers would trigger
Tier 2, and the dispatch's explicit resource constraint (**≈ 10 G RAM, no swap, "never run a large test farm"**) was
honoured instead — the lane's own review ran a scrubbed **26-file blast radius (2 019 P / 26 F)** with all 26 attributed.
*"This is the gate's largest deliberate scope reduction."*
(`.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md:159`)

⇒ **Run a BOUNDED dual-side check scoped to the `daemon/manager.py` reach — NOT a whole-tree sweep.** The box has ~10 G
RAM and zero swap, and test farms have been OOM ammunition.

**The scope — exactly the manager's reach into the skill services** (the files `daemon/manager.py` calls into for this
lane): `daemon/services/skill_deactivation.py`, `daemon/services/skill_capture_observability.py`,
`daemon/services/skill_job_dispatcher.py`, `daemon/services/skill_metrics_service.py`,
`daemon/services/skill_evolution_service.py`, `daemon/repositories/skill/repository.py`,
`daemon/repositories/job_queue/queue_repository.py`.

**The procedure** — run the lane's OWN registered packs, one at a time, serially, with a hard timeout, and **never** with
`PYTEST_XDIST_WORKER` set (the house rule: xdist ⇒ skip-all ⇒ SERIAL):

```bash
cd /home/nea/ensemble-src-wt-skill-revival-phase1
unset PYTEST_XDIST_WORKER
for pack in skill_stale_deactivation_unit_test skill_capture_observability_unit_test \
            skill_capture_killswitch_unit_test skill_evolution_unit_test \
            skill_counter_drift_unit_test skill_dual_column_deactivation_unit_test; do
  echo "=== $pack ==="
  timeout 900 bash "test/packs/${pack}.sh" 2>&1 | tail -3
done
```

**Expected:** every pack prints `RESULT: PASS`. **STOP if:** any pack prints `RESULT: FAIL` **and** the failure is not
base-proven — compare against the base-leg red set before concluding anything.

**The base-leg truth** (so you can tell a new red from a known one): the Phase-1 base leg's **TRUE base = 3 stable reds** —
the `tests/manager/test_skill_service_init.py` trio (the pre-existing SQLite `20260714_000001` migration defect,
the per-statement execution site `daemon/migrations/runner.py:677`), out of 459 collected (3 F / 456 P), stable across the base legs
(`phase1-plan.md` §13.2; gate report §1). **The base leg is NOT perfectly stable** — one base run showed 5 F / 454 P
because the AB-resolution flake fired in 1 of 3 runs; the classifier is `grep '^FAILED'` **only**
(`phase1-plan.md` §13.2, `decisions.md` DS-35).

### 8.4 The live-PG leg re-run — **BEFORE capture-on**

```bash
cd /home/nea/ensemble-src-wt-skill-revival-phase1
timeout 900 bash test/packs/skill_revival_pg_dialect_unit_test.sh
```

**What it does (house PG discipline — Docker is BANNED):** it provisions **its own disposable native PostgreSQL cluster** —
`initdb -A trust` into a `mktemp -d` datadir (`:159`), `pg_ctl` start on a **free private port** with a private socket dir
(`:166-167`), `createdb` the scratch DB **over TCP** (`:189`), a pre-run `SELECT current_database()` proof (`:208-216`),
and **teardown on EVERY exit path** (the `trap cluster_cleanup EXIT INT TERM`, `:141-147`). Default port **54333**
(`:136`), refused if busy (`:153-154`); scratch DB `ensemble_test` (`:137`) — the allowlisted name the shared PG conftest
accepts; **every `POSTGRES_*` var is scrubbed PER VARIABLE** (`:89`) because the `env -u POSTGRES_*` glob form is a **silent
no-op**. Exit codes: **0 PASS / 1 FAIL / 124 TIMEOUT** (`:72`).

**Why it is a PRE-FLIGHT item, not a nicety — the dialect split is runtime-BLIND on SQLite:**
swapping the two branches of `build_fix_admission_count_statement`
(`daemon/repositories/job_queue/repository.py:34-79`) makes **6 nodes RED on PostgreSQL with a HARD runtime error** —
`UndefinedFunction: function json_extract(jsonb, character varying) does not exist` — **while the SQLite runtime sees
nothing wrong**: the swapped SQLite branch compiles to `JSON_EXTRACT(metadata, '$."skill_id"')`, which **works** on
SQLite, so the count still returns 1. A SQLite-only suite therefore sees nothing wrong **at runtime** (only the
compile-time string pin catches it) — *"this is precisely why the live-PG leg exists"*
(`.agents/tester/PACKS.md:13`, the `skill_revival_pg_dialect_unit_test` row).

**STOP if** the pack prints **`RESULT: FAIL (MISSING-POSTGRES-BINARIES — no native initdb/pg_ctl/createdb found; the
live-PG leg is NOT VERIFIED on this host)`** (`:126-129`) — the host has no native PG binaries, so the dialect split is
**unverified** on this host and the capture flip must not proceed on that basis. (The gate recorded this leg as
**structurally un-runnable at gate time** and **runnable later on this host** — postgresql-16 binaries on disk,
`.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md:162-165`.)

### 8.5 The deactivation receipt's presence + the brake state

**The receipt path:** `<data_dir>/skill_stale_deactivation_receipt.json`
(`RECEIPT_FILENAME`, `daemon/services/skill_deactivation.py:157`; the resolver `resolve_receipt_path`, `:556-573`;
the `<data_dir>` chain `ENSEMBLE_DATA_DIR > DATA_DIR > ./data`, `daemon/services/skill_capture_observability.py:397-412`).
On the live install: **`/home/nea/agents-ensemble/data/skill_stale_deactivation_receipt.json`**.

**Companion file:** `<data_dir>/skill_stale_candidate_manifest.json`
(`CANDIDATE_MANIFEST_FILENAME`, `daemon/services/skill_deactivation.py:159`).

```bash
DATA_DIR=/home/nea/agents-ensemble/data
ls -l "$DATA_DIR"/skill_stale_deactivation_receipt.json "$DATA_DIR"/skill_stale_candidate_manifest.json 2>&1
cat "$DATA_DIR"/skill_stale_deactivation_receipt.json 2>/dev/null | head -40
```

**Expected:** the receipt is **present** after the E4 brake has been armed and the daemon has restarted, and it
**enumerates the refused A/B rows** (`skipped_open_ab_test` with the blocking test id and group — the refusal envelope
format is `scripts/audit/skill_stale_deactivate.py:177-181`).

**STOP if:** the receipt is **absent** after the brake was armed and the daemon restarted — the reconciliation did not
run (or the brake was not actually on). Check the daemon log for `[E4.1] skill stale-deactivation reconciliation
skipped: brake ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED is OFF (default)` (`daemon/manager.py:5794-5797`) — that line
means the brake was OFF.

**The brake state, re-read at the same moment:**

```bash
psql -Atc "SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id='bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';"
```

**Expected:** `is_paused = t` (armed) — unless you deliberately released it.

### 8.6 The capture-lane read-back after the restart

```bash
curl -sS http://127.0.0.1:9797/api/metrics/capture-lane
```
**Expected:** **200**, `counters.admission_refusals` **not** growing, `counters.admissions` growing once the lane has work.
**STOP if:** the daemon log shows `Invalid ENSEMBLE_SKILL_…` (`daemon/services/skill_job_dispatcher.py:143-151`) — the
switch value is invalid and the resolver will **raise** on first use (fail-closed).

---

## 9. WHAT MUST NOT HAPPEN

### 9.1 **No winner may be declared for the 27 stranded A/B pairs — and that refusal is a FEATURE**

* Choosing which variant of `code-fix` / `dev-strategy` / `git-commit` / `plan-creation` survives is the **Emperor's
  E-6 decision** (`phase1-plan.md` §16.3 E-6) — a quality decision with real consequences that belongs to the
  orchestrator, not to this lane and not to this runbook.
* **The mechanism REFUSES without it.** The pair-resolver refuses with `skipped_no_winner_decision`
  (`daemon/services/skill_deactivation.py:153-154`); the A/B-guarded rows are refused as `skipped_open_ab_test`
  (`:151-152`). **Zero call sites supply a winner** (gate report §8(d): *"no-winner: `PairResolutionRefused:
  skipped_no_winner_decision`; zero call sites supply a winner (static)"*).
* **"The 0 target is BLOCKED-ON the A/B resolution decision"** — verbatim, `daemon/repositories/skill/repository.py:863-865`.
* **⇒ THAT REFUSAL IS A FEATURE.** The refusal is the *proof* that no shipped call site can silently pick a winner
  (`decisions.md` DS-44, Ruling B). **Do not "help" the lane by resolving a pair.** A divergence gauge that drops below
  32 is a **STOP** condition (§5.2(a)), not an improvement.

### 9.2 **No CLI flag may be added to `scripts/audit/skill_stale_deactivate.py`**

The override is a **Python-API keyword only** (`allow_declared_artifact=True`). The tidier found that the docstrings
told the operator to pass a flag the script **never registered**; the leader ruled **REWORD, DO NOT ADD THE FLAG**,
because *"a CLI escape would contradict the pinned 'no winner declared by any shipped call site' property"*
(`phase1-plan.md` §17 item 43, H1). **The script registers `--db-url`, `--apply`, `--reactivate`, `--receipt`,
`--data-dir`, `--json` — and NOTHING ELSE** (`scripts/audit/skill_stale_deactivate.py:97-155`; a pin asserts the absence).
**Do not add one. Do not pass one.**

### 9.3 **The capture-axis has no key of its own**

**ONE `skill_injection` bit serves BOTH gates** — the automatic capture path
(`daemon/services/skill_metrics_service.py:715-729` — the `skill_injection_enabled` resolution and its refusal) **and**
the dynamic injection path (`daemon/services/instance_messaging.py:4312-4314`,
`daemon/services/context_messages.py:2538-2539`).

⇒ **Per-axis arming needs a SECOND key or an explicit coupling ruling.** There is no per-axis switch today, and this
runbook must not pretend otherwise: **flipping an agent's `skill_injection` to `true` makes that agent capture-eligible
AND injection-enabled at the same moment.** The plan's roster manifest is per-axis precisely so the Emperor can make that
decision in Phase 2 (`phase1-plan.md` §10 — *"The manifest must say, per agent, 'capture-enabled' and separately
'injection-enabled'"*).

### 9.4 Two more that are easy to get wrong at 3am

* **Never `rm` a sink file** — move it aside (§6.7). The gate lost 116 evidence rows to a whole-file unlink
  (gate finding **F-G1**, MAJOR, `.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md:147`).
* **Never delete a skill row.** The E4 mechanism only ever **deactivates** (`is_active=False, status='inactive'`) —
  `SkillRepository.deactivate` is the sanctioned dual-column writer (`daemon/repositories/skill/repository.py:383-396`);
  there is **no tombstone column** on `skills` and the mechanism **never hard-deletes** (`phase1-plan.md` §9 E4).

---

## 10. THE KNOWN RESIDUALS — what is **NOT** fixed

| # | Residual | Status | Citation |
|---|---|---|---|
| **R1** | **The F-4 import trap** — importing a `daemon.tools.*` module registers ~52 metric families in-process (a `daemon.tools` import takes the registry from 15 → 67 registered metrics) | **STILL OPEN, INERT.** The committed exposition artifact is rendered in a **SUBPROCESS** (`tests/helpers/metrics_exposition_baseline.py` spawns a fresh interpreter importing only `checkpoint_metrics` + `metrics_listener`), so an in-process registration cannot reach it; the decisive co-residency experiment was **GREEN** (58 passed). **The residual risk:** an in-process render DOES diverge (29 → 81 `# HELP` lines) — a FUTURE in-process-vs-artifact comparison would be the first live victim. The durable hardening is **ROUTED, not built**. | `daemon/tools/nest_report.py:158-162` (the attribute import at `:159`) · `decisions.md` DS-57, DS-59 · `phase1-plan.md` §17 item 35 |
| **R2** | **The per-lane sink redesign** — one shared daily sink file for every lane | **DISCLOSED FAST-FOLLOW, NOT BUILT** (leader ruling 2026-11-10). **Compensating controls:** (1) the landed **row-granular** fix (the name-date prune, commit `2432a5721`; the redirect-restore fix, commit `cf86ee25f`); (2) the **MOVE-FILES-ASIDE (never `rm`)** measurement procedure. | `phase1-plan.md` §17 item 37 (`:1037`) · `daemon/services/skill_capture_observability.py:490-491` · gate finding F-G1 (`…gate.md:147`) |
| **R3** | **`_READ_SCOPE_CACHE` same-tick + same-length staleness** — an edit that preserves the byte length AND lands inside the filesystem's timestamp tick can still be served stale. `st_ctime_ns` closes the `utime`-restore case but does **not** eliminate the window. | **OPEN, DISCLOSED (D-N15).** The cache is keyed on the meta.json's identity (path + `st_mtime_ns` + `st_size` + `st_ctime_ns`) and the `stat()` IS the invalidation trigger — so **no restart is needed** for a scope edit; only the narrow same-tick/same-length window survives. | `daemon/tools/filesystem.py:255-274` (the RESIDUAL disclosure), `:275` (the cache), `:332-344` (the lookup), `:347-372` (`_meta_identity_matches`) |
| **R4** | **Roster / `skill_injection` changes need a daemon restart** | **BY CONSTRUCTION.** `get_registry()` builds the registry **once per process** and `discover()` parses `meta.json` at that moment, freezing `skill_injection`. | `daemon/registry.py:1525-1545`, `:627-637`, `:690`, `:756` |
| **R5** | **The leaking test files (the sink-leak class)** — routed to the tester | **ROUTED TO THE TESTER for the final pass** — this lane does not own those files. The concrete instance: `test_p9_the_sentinel_catches_a_teardown_that_restores_none` poisons `tests/helpers/skill_sink_redirect.py::_LAST_LANE_STATE` and does not restore it in `finally` ⇒ 12 later errors in one session. | `phase1-plan.md` §17 item 37 (`:1037`) · gate finding **F-G9** (`.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md:150`) |
| **R7** | **The capture lane's dispatcher binding was COVERED-BY-TEST-SIDE-INJECTION ONLY** — every pre-Phase-3 pin injected the dispatcher into the `SkillMetricsService` from the test side, so a production-boot disconnect would not have reddened any suite | **CLOSED by Phase 3** (branch `feature/skill-capture-prod-wiring`): the binding is now made explicitly at the api boot seam (`daemon/api.py::_wire_skills_router_services`), pinned by a production-wiring test that drives the REAL seam, and pinned AGAIN at the call site (`lifespan` still calls the seam — round-2 F2 static pin), with **all three mutation legs proved RED** (leg 1 = binding removed; leg 2 = binding removed + test-side injection STILL RED; leg 3 = `lifespan` call line removed → ONLY the static pin reddens). **DISCLOSURE:** the manager-side binding (`daemon/manager.py:8196-8197`, inside `InstanceManager.set_job_queue_service`) was **already** present and already ran in production — the earlier "no production caller" reading was a grep-scoped miss, not a live defect; the Phase-3 work closes the COVERAGE hole, not a live break. | `daemon/manager.py:8196-8197` · `daemon/api.py` (`_wire_skills_router_services` + `lifespan` call) · `tests/unit/test_skill_capture_production_wiring.py` |
| **R6** | **The live-PG leg's first-run provisioning** — it self-provisions a disposable cluster, so **no pre-existing PG server is needed**, but it **REQUIRES native PG 16 binaries** on the host | **DISCLOSED.** If the binaries are absent the pack prints *"the live-PG leg is NOT VERIFIED on this host"* and exits 1. | `test/packs/skill_revival_pg_dialect_unit_test.sh:115-131` (the binary discovery + the message), `:136` (port 54333), `:141-147` (teardown), `:189` (createdb over TCP) |

> **OPEN ITEM (R5's count).** The leader's brief calls these *"the four leaking test files"*. **The number four could not
> be verified in this worktree**: by my own census, **6** test files import the sink-redirect helper
> (`tests/unit/test_skill_revival_wave2_pins.py`, `tests/unit/test_skill_revival_fix_wave1_pins.py`,
> `tests/unit/test_skill_sink_retention_shared_path_pins.py`, `tests/unit/test_skill_fix_burst_guard.py`,
> `tests/unit/test_skill_stale_deactivation.py`, `tests/services/test_skill_capture_observability.py`).
> **What would settle it:** the tester's own brief / the PACKS.md rows for the final pass. **The CLASS is what matters
> operationally** (the shared-sink unlink), not the count.

> **DISCREPANCY REPORTED (R3/R4).** The leader's brief listed *"D-N10 scope edits need a restart"* as a residual.
> **No `D-N10` label exists anywhere in this worktree** (0 hits across `*.py` and `*.md`), and the shipped code says the
> OPPOSITE for `read_scope`: the cache's `stat()` invalidation means **a scope edit does NOT need a restart**
> (`daemon/tools/filesystem.py:261-264`). The restart-requiring surface is the **roster** (`skill_injection`), which is
> R4 above. The accurate residual is the **same-tick + same-length staleness** (R3).

---

## 11. PRE-FLIGHT CHECKLIST + THE STOP LIST

### 11.1 PRE-FLIGHT — must be **all green** before ANY flip

| # | Check | Command / read | Green means |
|---|---|---|---|
| **P1** | The **release is live** | `readlink /home/nea/agents-ensemble/current` + `state.json`'s `current` | the Phase-1 release |
| **P2** | The **project id** for `agents-ensemble` is known | `psql -Atc "SELECT project_id, name, job_queue_paused FROM projects WHERE name='agents-ensemble';"` | exactly one row; `job_queue_paused = f` |
| **P3** | The **queue row** exists and its state is known | the §3.2 read-back | one row; `system_parallel_queue` |
| **P4** | The **brake is armed and verified** | re-run the §3.2 read-back after arming | `is_paused = t` |
| **P5** | The **metrics door answers** | `GET /api/metrics/capture-lane` | **200** (not 503) |
| **P6** | The **counter baseline** is captured | the §6.8 "BEFORE" block | `/tmp/capture-lane.before.json` exists |
| **P7** | The **A4 gauge** is at the baseline | `--measure` | `diverging_keys = 32`, `drift` recorded |
| **P8** | The **A3 candidate band** (`feedback_applied IS NULL` — the only predictive, capture-eligible population) is non-empty | the §8.1 SELECT (a) | `> 0` — **STOP if 0** (live ≈3 544 rows / 3 525 instances, 2026-11-10T21:52Z) |
| **P9** | The **live-PG leg** is green here | `bash test/packs/skill_revival_pg_dialect_unit_test.sh` | `RESULT: PASS` |
| **P10** | The **bounded dual-side check** is green | the §8.3 pack loop | every pack `RESULT: PASS` |
| **P11** | The **sink** is present and readable | `ls -la /home/nea/agents-ensemble/data/skill-capture-observability/` | readable |
| **P12** | The **switch edits** are staged but NOT yet applied | `grep -n '^ENSEMBLE_SKILL' /home/nea/agents-ensemble/.env` | both switches currently OFF/absent |
| **P13** | The **audit log** is open | — | a file where every step's timestamp, operator and read are recorded |

### 11.2 THE STOP LIST — if ANY of these is true, **DO NOT FLIP**

1. The release is **not live** (§12 — this is a ceremony, not a flip).
2. The capture-lane metrics read returns **503** (no counter read ⇒ no go/no-go).
3. The A4 divergence gauge is **not 32** — **> 32** is drift, **< 32** is an undeclared winner.
4. The A3 **candidate band** (`feedback_applied IS NULL`, §8.1(a)) is **0** — nothing is left to capture, so the lane can never admit. (The **blocker band** `feedback_applied IS NOT NULL`, §8.1(b), is a MONITOR ONLY: its non-zero value is NORMAL and is **never** a stop condition.)
5. The live-PG pack prints **NOT VERIFIED on this host**.
6. Any bounded dual-side pack is **red** and the red is **not base-proven**.
7. The brake read-back does not show **`is_paused = t`**.
8. The evolution switch is **ON** while you intend to bring up **capture only**.
9. The **anomaly surface** is not quiet — a `tool-failure` (deviation) or `queue-scheduling` (burst cap) row appeared in
   the last window.
10. `admission_refusals["capture_lane_off"]` **increased** during the window — the lane flapped.
11. `execution_refusals["lane_disabled"]` **increased** — something was admitted and then refused at execution.
12. The **sink** shows `"deviation": true` on any row.
13. You **cannot** re-read the A4 gauge (script missing, DB unreachable, venv absent) — the measurement surface must work
    BEFORE you flip, not after.
14. The daemon log shows **`Invalid ENSEMBLE_SKILL_…`** — the switch value is invalid and will **raise** on first use.

---

## 12. THE PROMOTE IS NOT THIS RUNBOOK

**The promote is a SEPARATE, Emperor-owned 3-factor nonce ceremony.**

* It is executed **from the scripts-pin worktree** (`/home/nea/ensemble-src-wt-scripts-pin`) — **CWD selects the
  executing script**, so it must NOT be run from this feature worktree (which would execute modified ceremony code) and
  MUST NOT be run from the canonical dir (`phase1-plan.md` §14.3).
* The canonical dir is **arm-INELIGIBLE** on three independent counts (`decisions.md` DS-28; `phase1-plan.md` §14.3).
* **THIS RUNBOOK ASSUMES THE RELEASE IS ALREADY LIVE.** It contains **no promote command**, no nonce, no arm, no
  rollback-to-previous-release. If the release is not live, go to the ceremony.
* The **pre-promote lever** (revert + re-promote) is named in §7 for completeness only — it is a ceremony act, not a
  switch-flip act.

---

## Appendix A — command quick card

```bash
# ── 0. context ────────────────────────────────────────────────────────────
INSTALL_DIR=/home/nea/agents-ensemble
WT=/home/nea/ensemble-src-wt-skill-revival-phase1
QUEUE_ID=bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2     # PK
QUEUE_NAME=system_parallel_queue                  # the name has NO "skill" in it
cd "$WT"

export PGHOST=$(grep -m1 '^POSTGRES_HOST=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGPORT=$(grep -m1 '^POSTGRES_PORT=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGDATABASE=$(grep -m1 '^POSTGRES_DB=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGUSER=$(grep -m1 '^POSTGRES_USER=' "$INSTALL_DIR/.env" | cut -d= -f2-)
export PGPASSWORD=$(grep -m1 '^POSTGRES_PASSWORD=' "$INSTALL_DIR/.env" | cut -d= -f2-)
DB_URL="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
PROJECT_ID=$(psql -Atc "SELECT project_id FROM projects WHERE name='agents-ensemble';")

# ── 1. READ the brake ─────────────────────────────────────────────────────
psql -Atc "SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id='$QUEUE_ID';"

# ── 2. ARM the brake (pick ONE) ───────────────────────────────────────────
#  (a) REST:
curl -sS -X PATCH "http://127.0.0.1:9797/api/projects/$PROJECT_ID/queues/$QUEUE_ID" \
     -H 'Content-Type: application/json' -d '{"is_paused": true}'
#  (b) agent tool:
#      queue_update(queue_id="$QUEUE_ID", project_id="$PROJECT_ID", is_paused=true)
psql -Atc "SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id='$QUEUE_ID';"   # expect t

# ── 3. READ the counters + the gauges ─────────────────────────────────────
curl -sS http://127.0.0.1:9797/api/metrics/capture-lane | tee /tmp/capture-lane.before.json
.venv/bin/python scripts/audit/skill_counter_reconcile.py --db-url "$DB_URL" --measure --json | tee /tmp/a4.before.json
psql -Atc "SELECT count(*) FROM anomaly_reports WHERE category IN ('tool-failure','queue-scheduling') AND instance_id = 'daemon:skill-capture-observability';"

# ── 4. FLIP (edit the live .env, then restart) ────────────────────────────
#    ENSEMBLE_SKILL_CAPTURE_ENABLED=1
#    ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED=1      (TOGETHER, never before)
#    ENSEMBLE_SKILL_EVOLUTION_ENABLED stays OFF for Step 1
sudo systemctl restart ensemble-main.service           # confirm the unit name first:
systemctl list-units --type=service 'ensemble-*' --no-pager

# ── 5. RE-READ the baselines (the counters reset on restart) ──────────────
curl -sS http://127.0.0.1:9797/api/metrics/capture-lane | tee /tmp/capture-lane.t0.json
ls -l "$INSTALL_DIR"/data/skill_stale_deactivation_receipt.json
```

**The unit name is host-specific.** `ensemble-main.service` is the shape used by the house runbooks
(`docs/runbooks/hard-reboot-recovery.md:52`, `docs/runbooks/resource-hygiene-operator-checklist.md:68`); **confirm it
first** with `systemctl list-units --type=service 'ensemble-*'`
(`docs/runbooks/checkpoint-blob-prune-restore.md:270`). On this host the live install is `/home/nea/agents-ensemble`
with `current -> releases/v0.29.7` at the time of writing.

---

## Appendix B — citation index (every fact in this document, and where it came from)

| Fact | Citation |
|---|---|
| Job-queue poll interval 30.0 s | `daemon/services/job_processor.py:119`, `:138` |
| The queue row is re-read every poll cycle (`if queue.is_paused: continue`) | `daemon/services/job_processor.py:957` (loop head `:956`) |
| Project-level brake (second line of defence) | `daemon/services/job_queue_service.py:4516`, `:5468` |
| The brake lever: `queue_update(..., is_paused=...)` | `daemon/tools/job_queue.py:2360-2367` (field `:2365`, write `:2393-2394`), doc `:328-338` |
| The REST lever | `daemon/routers/queues.py:324-397` (prefix `:37`; `is_paused` `:365-366`; WRITE_PAUSED `:355`; 404 `:379-382`), mounted `daemon/api.py:5544` |
| The write path | `daemon/services/job_queue_mgmt_service.py:366-418` → `daemon/repositories/job_queue/queue_repository.py:239-273` (`updated_at` always refreshed `:270`) |
| The queue PK / name / paused columns | `daemon/repositories/job_queue/models.py:201-204`, `:208`, `:215` |
| The alias map (`system_parallel_queue`) | `daemon/services/queue_ref.py:60-72`, `:86-130` |
| `job_pause` is NOT the brake | `daemon/tools/job_queue.py:805`, `:3895-3902` |
| The capture switch name + resolver | `daemon/services/skill_job_dispatcher.py:75`, `:107-121`, `:153-176` |
| The evolution switch (canonical + alias) | `daemon/config.py:5226-5233`, `:5235-5241`, `:5243-5261`; prefix `:5148` |
| The E4 brake name + resolver + inert-when-off | `daemon/services/skill_deactivation.py:127-129`, `:132-149` |
| The E4 boot call site | `daemon/manager.py:2786`, `:5768-5800` (brake check `:5792-5797`) |
| The `.env` is parsed once per launcher process | `launcher.sh:194-237` (parser), `:3351` (call); `docs/runbooks/systemd-adoption.md:194-199` |
| The unit carries no `.env`-derived `Environment=` | `docs/runbooks/systemd-adoption.md:183-192` |
| The roster is a process-lifetime snapshot | `daemon/registry.py:1525-1545`, `:627-637`, `:690`, `:756` |
| The capture-lane-off counter — 3 sites, 1 key | `daemon/services/skill_metrics_service.py:663`/`:682`/`:687` · `daemon/tools/skill_evolution_tools.py:522`/`:539`/`:563` · `daemon/services/skill_job_dispatcher.py:1126`/`:1136`/`:1148` · key `daemon/services/skill_capture_observability.py:291-292`, `:355` |
| The counter store (process-global, cumulative) | `daemon/services/skill_capture_observability.py:241`, `:249-264`, `:279-285` |
| The four metrics routes + auth/scope | `daemon/routers/metrics.py:836-837`, `:581`, `:630`, `:703`; scopes `:106`, `:116`; fail-closed `:136-152` |
| The execution-refusal row + counter | `daemon/services/job_processor.py:788`, `:818`, `:867`; scope `:1437` |
| The deviation announcement | `daemon/services/skill_capture_observability.py:754-812` (accept call `:796-806`); category `:146`; emit site `daemon/services/skill_evolution_service.py:1419-1434` |
| The burst-cap signal chain | `daemon/services/skill_job_dispatcher.py:781-792` (WARNING), `:793` (counter), `:794-807` (sink), `:802-812` (anomaly); category `daemon/services/skill_capture_observability.py:147` |
| The 429 (not 500) | `daemon/routers/skills.py:1364` (route), `:1390-1399`, `:1465-1485` |
| The burst-cap defaults + env aliases | `daemon/config.py:5295-5307`, `:5308-5318`, `:5148` |
| The FIX-admission gauge vs the durable count | `daemon/services/skill_capture_observability.py:304-308` (gauge), `daemon/services/skill_job_dispatcher.py:1079` (call) · `daemon/repositories/job_queue/repository.py:659-723` (durable), `:34-79` (builder) |
| The A4 gauge | `daemon/repositories/skill/repository.py:803-847` (`:843-846` the 32/0 baseline), `:848-861` (32-vs-27 + residual 7), `:863-865` (BLOCKED-ON), `:876-889` (the query) |
| The A3 predicate + the 3 457 window | `daemon/repositories/skill/repository.py:1699-1719` (`:1717-1719`) |
| The capture-axis `skill_injection` gate (one bit, two gates) | `daemon/services/skill_metrics_service.py:715-729` · `daemon/services/instance_messaging.py:4312-4314` · `daemon/services/context_messages.py:2538-2539` |
| The reconcile script's CLI | `scripts/audit/skill_counter_reconcile.py:371-421`; `--db-url` `:379-387`; modes `:388-411`; `--receipt` `:407-412`; `--json` `:412-421` |
| The 24 h convergence gate + exit 3 | `scripts/audit/skill_counter_reconcile.py:102`, `:329-363`, `:487-493` |
| The reconcile instrument's scope (A4) | `scripts/audit/skill_counter_reconcile.py:197-224`, `:136-152` |
| The stale-deactivate script's CLI (no winner flag) | `scripts/audit/skill_stale_deactivate.py:97-155`; refusal envelope `:177-181` |
| The receipt path + data-dir chain | `daemon/services/skill_deactivation.py:157`, `:159`, `:556-573`; `daemon/services/skill_capture_observability.py:397-412` |
| The sink (dir, file pattern, retention, prune) | `daemon/services/skill_capture_observability.py:151`, `:155`, `:157-161`, `:472-509`, `:490-491`; record shape `:825-868` |
| The roster payload's capture gate | `daemon/services/skill_roster.py:372-383`, `:385-395` |
| The PG pack (cluster, port, teardown, messages) | `test/packs/skill_revival_pg_dialect_unit_test.sh:72`, `:89`, `:115-131`, `:136`, `:137`, `:141-147`, `:153-154`, `:159`, `:166-167`, `:189`, `:208-216` |
| The dialect split is runtime-blind on SQLite | `.agents/tester/PACKS.md:13` (the `skill_revival_pg_dialect_unit_test` row, mutation M-1) |
| The release payload contains no `scripts/audit/` | `scripts/upgrade/stage.sh:395-425` |
| The gate's scope reduction | `.agents/tester/RESULTS/2026-11-10-skill-revival-phase1-tester-gate.md:159` |
| The gate's findings F-G1 / F-G3 / F-G9 | same file `:147`, `:152`, `:150` |
| The gate's base-leg truth (3 stable reds) | same file §1; `phase1-plan.md` §13.2; `decisions.md` DS-35 |
| The F-4 import trap | `daemon/tools/nest_report.py:158-162` (`:159`); `decisions.md` DS-57, DS-59 |
| The `_READ_SCOPE_CACHE` residual | `daemon/tools/filesystem.py:255-274`, `:275`, `:332-344`, `:347-372` |
| The per-lane sink redesign (fast-follow) | `phase1-plan.md` §17 item 37 (`:1037`) |
| The plan's §15 content spec (the source of this document) | `.agents/shared/planning/skill-system-revival/phase1-plan.md:938-957` |
| The brake, by ID not by name LIKE | `phase1-plan.md` §7 (C1/C2); `decisions.md` DS-41 |
| The 27 pairs / E-6 / "no winner in Phase 1" | `phase1-plan.md` §9 E4, §16.3 E-6; `decisions.md` DS-44 |
| The observation window (≥ 24 h) | `phase1-plan.md` §15 step 2; `scripts/audit/skill_counter_reconcile.py:102` |
| The promote is a separate ceremony | `phase1-plan.md` §14.3; `decisions.md` DS-28 |

---

*Companion to `phase1-plan.md` (§15) and `decisions.md` (DS-62). Every `file:line` was opened in
`/home/nea/ensemble-src-wt-skill-revival-phase1`. This document is **uncommitted on purpose** — the Leader owns the
commit point.*
