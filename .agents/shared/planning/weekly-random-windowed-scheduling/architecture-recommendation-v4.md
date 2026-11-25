# Architecture Ruling v4 — weekly-random windowed scheduling (FINAL of record)

**Supersedes v3** (v1/v2/v3 kept for audit trail — keep-both, newest-first). Design of record for the builder lane.

- **Date:** 2026-11-23 (~20:45Z) · **Lane:** architect (controller) · **Workers:** `ac129835` (source audit + follow-up), `43563213` (collision-probe census)
- **Evidence trees:** `origin/latest` @ `27f9130` and @ `196839c`; live release v0.31.42 = source commit `ea5325a` (ancestor of `origin/latest`; the release tree is a **compiled binary**, so live code was read at its recorded `source_commit` — scheduler code identical to origin/latest on every point this ruling turns on).
- **What changed in v4:** (i) the collision measurement is now **specified and cheap** (what to ask, of whom, and the pass/fail criteria); (ii) the census's "the house already jittered this lane" claim is **adjudicated — the precedent cuts the other way on operability**, and it **collapses Phase 2** from "build a generic windowed capability" to "re-enable the native chain with its two missing fixes"; (iii) the cross-lane routing keeps the seed's project scope as the defect's origin.

---

## The short version (for the Emperor)

**The feature already exists** — natively, and it is the house's *default* for this exact schedule: the Central Maintenance weekly is a jittered chain row, picked at a uniform-random minute inside Sunday 01:00–05:00 ICT and re-armed by the scheduler itself (`maintenance_schedule_knobs.py:88-91`, `maintenance_seed.py:135`, `scheduler.py:604-613/888-1070`). You do not need to "implement the feature if it does not exist."

**Easier — no.** A fixed cron slot is one string that already re-arms, survives restarts and handles DST. Jitter needs pick + persist + re-pick + restart-safety + window edges.

**Better — only against a measured, clock-pinned collision.** Two facts now decide it: the house's PG-side competitors are **threshold-driven** (autovacuum `scale_factor=0.05 / threshold=1000`) and **IDLE-gated** (the vacuum sweep) — so a fixed slot can only *coincide* with them, never deterministically collide; and the one genuinely clock-pinned recurring thing in that window is **the maintenance weekly itself**. The remaining candidates are **platform-side** (Longhorn recurring snapshots on the PVC, host timers) and are **not visible from the repo** — so the measurement must ask the platform, and it is cheap: one `kubectl GET`, one read-only SQL pass, one `systemctl list-timers`, over one weekly cycle.

**And the precedent cuts against more machinery, not for it:** the jittered chain is the row that was **cancelled tonight** and replaced by the fixed canonical cron (`central-maintenance-weekly-v2`, Sunday 03:30 ICT). It was not replaced because jitter was wrong — it was replaced because its **operator surface lied** (weekly read as "once") and its **spawn stranded a task in the system-default project**. Jitter's real cost is operability. So if the platform probe finds a genuine collision, the remedy is **re-enabling the chain that already exists** (with those two fixes), **not building a new scheduling capability**.

---

## 0. Source check (carried from v3; unchanged and independently corroborated)

Chain-mode `weekly-random` is native daemon code: `maintenance_seed.py:135 CHAIN_MODE`; the chain block is written only by the seeder (`build_chain_config` :390-432, `refresh_chain_keys` :349-384) and read only by the adapter (`scheduler.py:608`); the adapter re-arms natively (`_handle_consumed_one_time` :888-948 → `_rearm_chain_row` :950-1070). The live chain row's config key set matches the seeder's output **exactly (9/9)**; it carries no `creator_instance_id` (corroborating but **not** dispositive — REST-created and pre-capture rows also lack it) and no `schedule` key ⇒ not the agent-tool path. Effective interval **5–13 days**, not 7. Restart-safe (persisted pick + stable idempotency key + partial UNIQUE index). Listing misreports: stored `recurrence:"once"` echoed verbatim (`scheduling_service.py:694`) and no window field on `ScheduleDetail` (:194+). Next-fire is unreliable by construction: the **REST list** reads the registered adapter's in-memory `_get_next_trigger_time` (`routers/schedules.py:131-138`; cancelled rows never get an adapter, `registry.py:292`) while the **tool path** computes from the stored config (`_compute_next_run` :468-520). Cancel keeps the name (`repository.py:604+`; create-time uniqueness matches dead rows, `scheduling_service.py:736-742`).

## 1. Is windowed-random EASIER? — **No.** (unchanged)

## 2. Is it BETTER? — the decision-relevant form, now with a measurement

**2.1 Rule (stands).** **Jitter earns its complexity only against a MEASURED recurring collision** — and the collision must be with **clock-pinned** recurring work, because only clock-pinned work can be avoided by a random pick. Threshold-driven or IDLE-gated work is not avoided by jitter; it is merely not collided with.

**2.2 What the repo says about the competitor set (measured by the census; anchors verified by me):**
| Competitor | Shape | Clock-pinned? | Evidence |
|---|---|---|---|
| PG autovacuum on the toasted relation | **threshold-driven** (`scale_factor=0.05`, `threshold=1000`) | **No** | `daemon/migrations/versions/20261011_102502_set_checkpoint_trio_autovacuum_reloptions.sql:37,80-81` |
| House vacuum sweep (`VacuumMaintenanceJob`) | **IDLE-gated** + interval | **No** | `daemon/services/vacuum_maintenance.py:58` (`_is_idle` gate), `:410` qualifying-table SQL |
| Other daemon recurring jobs | interval + IDLE-gated (15-min tick; 24h/5h/1h jobs) | **No** | census of `asyncio.create_task` cadences + maintenance job registration (`manager.py:4851-4856`) |
| **The Central Maintenance weekly itself** | wall-clock-window-bound (Sun 01:00–05:00 ICT), **already jittered**, no idle gate | **Yes** | `maintenance_schedule_knobs.py:88-91`, `:63-66` ("NO idle/busyness precondition … fires regardless of system busyness"), `maintenance_seed.py:169` (dispatches the five lane children) |
| Longhorn recurring snapshots on the PVC | **unknown — platform-only** | **unknown** | **zero** repo tooling (exhaustive grep: `longhorn`, `volumesnapshot`, `VolumeSnapshotClass`, `recurring`, `restic`, `lvm`, `ceph` → no hits) |
| Host systemd/cron timers | **unknown — host-only** | **unknown** | repo precedent for the read: `docs/runbooks/install-dir-protection-layer1-arm.md:66` (`systemctl list-timers 'ensemble-*'`) |

⇒ **Conclusion:** the collision cannot be settled from the repo. It must be measured on the platform. Default posture until then: **fixed slot**.

**2.3 The measurement — cheap, read-only, devops lane** (recipe grounded in the census; one weekly cycle, ideally two):
1. **Platform (the real gap):** `kubectl -n longhorn-system get recurringjob,volumesnapshot -o wide` + Longhorn `actualSize` vs `df` (one GET, seconds; needs kubeconfig RBAC read).
2. **PG side (reuse, do not reinvent):** one read-only catalog pass over the prod DB reusing the house's own SQL — `docs/runbooks/checkpoint-maintenance.md:385-392` (`n_live_tup`, `n_dead_tup`, `last_autovacuum`, `pg_total_relation_size(relid)`, `pg_relation_size(c.reltoastrelid)`); use only the SELECT at `:108-112`, never the ALTER. Per-table size/dead-tuple shape also available via `daemon/tools/ens_db_tools.py:1214-1231`.
3. **Host:** `systemctl list-timers --all --no-pager`.
4. **House:** read `maintenance_runs` history for the actual fire phases of the interval/IDLE-gated jobs.
5. **The schedule itself:** read the `source_configs` row's `chain.window_start/end/weekday/timezone` and `run_at_utc` (read-only). Note `run_at_utc` legitimately lands on the **prior UTC date** (`central-maintenance-manual-trigger.md:110-112`: Sunday 04:15 ICT = Saturday 21:15Z).

**Attribution rule (anti-noise):** an observation counts as a *collision* only if the **same cause recurs at the same wall-clock minute across ≥2 cycles with a matching durable artifact** (Longhorn snapshot record / `maintenance_runs` row / `schedule_executions` row). A one-off dead-tuple spike is noise; a coincidental autovacuum start is noise.

**Pass/fail criteria (what would change the ruling):**
- **FAIL ⇒ build/re-enable jitter** if any of: a Longhorn recurring-snapshot job or host timer fires within ±15 min of the slot on ≥2 consecutive weeks, overlapping the reclaim window; **or** a PG-side maintenance job is found clock-pinned near the slot (repo says none exists — platform-only check); **or** the weekly's own re-rolled `run_at_utc` lands inside the reclaim I/O window on ≥2 sampled weeks **and** its `lane-disk-sweep` is confirmed I/O-heavy on the same PVC.
- **PASS ⇒ keep the fixed slot** if every competitor cause is threshold/interval/IDLE-driven with no wall-clock phase, no platform snapshot schedule is discoverable, and no timer recurs at the slot.

**2.4 Adjudication of the census's "the house already solved this with jitter" claim — accepted as precedent, REJECTED as conclusion.** The mechanism and the window are real and are the house's default for a fresh install (the boot guard is an "archive-aware **get-or-recreate**", `maintenance_seed.py:619` docstring) — that is why "implement the feature if it does not exist" has a concrete answer. But the jittered chain row is the one **cancelled tonight (067cdc8d)**, replaced by the fixed canonical cron `central-maintenance-weekly-v2` ("30 3 * * 0"). It was replaced on **operability** grounds (mislabeled listing; spawn stranded a task in the system-default project), not because jitter was wrong. The precedent therefore teaches the acceptance bar for any jitter re-enable — **the surface must tell the truth and the spawn must honor the project** — and it teaches that the house does not want *new* scheduling machinery for this lane. Also: "re-rolled weekly" is imprecise — the chain's effective interval is 5–13 days.

## 3. Canonical shape — and how much of it is actually left to build

**3.1 What the chain path is:** **native-but-half-exposed** — the scheduler adapter itself understands `chain` and re-arms it (`:604-613`, `:888-948`, `:950-1070`); only the maintenance boot seeder creates one; no user-facing creation path accepts a chain/window; and `ScheduleDetail` has no window field, so the operator surface calls a recurring task a one-shot.

**3.2 The ruled canonical shape:** **ONE user-facing mechanism** — `recurrence` (once | daily | weekly | cron) + local time + timezone, with an optional `window` field **only if** the platform probe justifies it for arbitrary schedules. The `chain` marker stays an **internal compatibility representation** — never a second user-visible mechanism.
- **Cheap half (now):** the window is already in the stored config ⇒ exposing it on the operator surface costs no new scheduling machinery. Build the truth-telling surface.
- **Expensive half (conditional, and now SMALLER):** if the probe FAILS, the remedy is **re-enabling the existing native chain** for the maintenance weekly — with the listing-truth fix and the spawn/project fix — **not** a generic windowed capability. The re-arm, the pick, the DST-safe anchoring, and the fail-closed window checks all exist.
- **If the 5–13-day spread is wanted** after any simplification, the adapter already supports a native interval primitive (`interval_seconds`, `scheduler.py:544/569-574/1130-1133`; cron wins when both are present) — a sanctioned one-line alternative to the chain marker.
- **Explicitly NOT ruled for:** a general-purpose user-facing "schedule inside a random window" capability. It is *deferred*, not denied — and nothing in this lane needs it.

**3.3 The listing floor — non-negotiable, on EVERY surface:** (1) recurrence truth; (2) window, when present (start, end, weekday, timezone); (3) ACTUAL next-fire from **one** authoritative source (the stored config), non-null for an armed recurring row, identical on tool and REST surfaces; (4) last-fire + status; (5) **project scope** — a listing that omits scope is the same misread class (§7).

## 4. Implementation spec (binding for the builder lane)

### Phase 1 — BUILD NOW (truth-telling surface; no new scheduling capability)
- **4.1 Listing truth.** `_row_to_detail` (`scheduling_service.py:672-710`): derive the effective recurrence when a `chain` block is present (never echo the stored "once" as the only truth); add a `window` field to `ScheduleDetail` (:194+) echoed from the chain block; make the next-fire **authoritative from the stored config on the REST list too** (repair `routers/schedules.py:131-138` so both surfaces share one source); surface `project_id`.
- **4.2 Namespace.** Scope create-time label uniqueness to **live** rows only (`scheduling_service.py:736-742`) **and** make label resolution prefer the **live** row (the cancel/update-by-label path is "first match wins" — with dead names reusable, that becomes a mis-target hazard).
- **4.3 Seed writes canonical rows.** The maintenance seed stops composing chain-shaped rows for new schedules and writes a canonical recurring row (`recurrence=weekly` + cron + local_time + timezone) under a **distinct label**; the archive-aware guard keeps its rule that an archived CANCELLED chain is never revived, so the cancelled chain row stays dead. The chain marker remains readable for legacy rows.
- **4.4 Pins (mutation-verified).** Listing pin: a chain-shaped fixture renders derived recurrence + window + a **non-null** next-fire + project scope on **both** surfaces; mutate the derivation out, or reintroduce an adapter-only next-fire source ⇒ pin reds. Namespace pin: create with a cancelled row's label succeeds; with a live row's label fails; label resolution prefers the live row. Seed pin: a fresh seed writes a canonical row with no `chain` block and a distinct label.

### Phase 2 — CONDITIONAL (only if the platform probe FAILS)
- **4.5 Run the collision probe** (§2.3) — devops lane, read-only, one weekly cycle; pass/fail criteria in §2.3. **A FAIL is the only trigger.**
- **4.6 On FAIL: re-enable the native chain for the maintenance weekly** — with §4.1's listing truth and §7's spawn/project fix in place; re-enable under a **distinct label** (the cancelled row keeps its name for history). Re-arm machinery, pick semantics, DST-safe anchoring and fail-closed window checks already exist — **no new scheduling capability is built.**
- **4.7 The re-arm proof leg.** Harness: fire one chain cycle ⇒ assert a NEW persisted `run_at`/`chain.run_at_utc`, weekday-anchored, inside the window, status RUNNING, new idempotency key; mutate the re-arm out ⇒ leg reds.
- **4.8 NOT in scope:** generic user-facing windowed scheduling (deferred, §3.2).

## 5. Migration + namespace (PROPOSED — do NOT execute)

- **`central-maintenance-weekly-v2` stays armed on the proven cron** (Sunday 03:30 ICT). It already sits inside the desired 01:00–05:00 window; nothing to migrate.
- **The cancelled chain row (`central-maintenance-weekly`, 067cdc8d) stays cancelled** — the archive-aware guard never revives an archived CANCELLED chain, and §4.3 preserves that rule. History stays greppable.
- **If the probe FAILS and §4.6 proceeds:** dual-armed cutover — the cron row stays armed until the FIRST jittered fire produces its receipt.

## 6. Guideline obligation — owed NOW

The chain machinery is exactly the house's "silently-biting rules" class. The guideline must state: chain-mode `weekly-random` exists, is native, and is the house's **default** for the maintenance weekly (boot guard get-or-recreate); only the maintenance boot seeder creates one (no `creator_instance_id` stamp — agent-facing creates always carry one); its window defaults to Sunday 01:00–05:00 ICT with **no idle gate**; its effective interval is 5–13 days, not 7; it is never disabled by a fire; until §4.1 lands, listings render it as `recurrence:"once"` with no window — read the REST config echo or the row's `chain` block for truth; cancelling is terminal and **keeps the name** until §4.2 lands. POK entries are filed; the human-facing guideline goes through the critical-note lane.

## 7. Cross-lane routing (evidence only — NOT taken here; leader's lane owns the fix)

The scheduled lane came up in the **system default project** and its task sat `pending`/unclaimed with its message `ready` and its agent loop never starting. **This lane traced the writer / re-arm / listing, not the spawn-claim path.** The evidence points upstream of the spawn: the **seeder** resolves `project_id = project_repo.ensure_system_default_project()` (`maintenance_seed.py:653`) and writes it into the row (`:416`) — so the row carries the default project and the adapter **faithfully** propagates it (`scheduler.py:455` → `:2966` via `_route_via_job_queue`). Routing items for the owning lane:
- **(a) the seed's project scope** — a seeded maintenance schedule should carry its own project, not the system default. *This is the defect's origin.*
- **(b) a second, genuinely unscoped path** — with `_project_id` empty **and** `instance_mode = NEW_INSTANCE`, the scheduler falls through to `_execute_immediate` (`scheduler.py:2823-2832`) with **no job-queue and no project scoping**; the durable branch is taken only when a project id exists or the instance is reused. Two branches, two different incubations — the pending/unclaimed symptom belongs to the job-queue branch.
- **(c) the listing must show project scope** (§3.3 item 5).

## Gaps / degraded / confidence

- `w-tradeoff` ran **DEGRADED — skill bank miss (`trade-off-analysis`)**; the bank has no design-dimension skills at all. Method inline; overlap claims independently confirmed.
- **The collision itself is unmeasured** — the named follow-up that decides Phase 2; the recipe and pass/fail criteria are now specified (§2.3).
- The operator's exact null next-fire was not reproduced; what is reproduced is the **two-source divergence class** (§0).
- The census's "already jittered" claim was **adjudicated, not accepted** (§2.4) — the precedent's operative lesson is operability, and it *shrinks* Phase 2 rather than enlarging it.
- **Confidence:** High on existence / writer / re-arm / listing / namespace / restart-safety / competitor shapes (all read directly in both trees). High on the canonical shape's size. **Medium** on Branch A/B — that is exactly what the platform probe measures, and it is the only input that changes what gets built.

---

## ADDENDUM A — Execution-Receipt Contract (Emperor-mandated, 2026-11-23 ~21:05Z; part of this ruling of record)

**Grounding and relation to tonight's record.** This addendum extends the canonical shape's contract to cover **both directions in time**: §3.3 governs *what happens next*; Addendum A governs *what actually happened last time*. **No contradiction with tonight's audits:** my workers traced the writer / re-arm / listing paths and never traced the executions ledger — the Emperor's live-DB census (47 of 51 fired sources with `instance_id` NULL on every row; `last_execution_status` reading `queued` for ran, never-dispatched, and proven-never-started alike) fills a gap my record left open. What I verified directly for this addendum, at `origin/latest` `27f9130`: the executions model (`daemon/repositories/source/models.py:161` — `schedule_executions`, indexed on `(schedule_id, status)` and `triggered_at`), the listing's source for the field (`_last_execution_status`, `scheduling_service.py:635-656`), and the model's own status-column comment (`models.py:52` — "PURE PYTHON over the existing TEXT column: `schedule_executions.status`").

**The root cause, in the code's own words.** `_last_execution_status`'s docstring (`scheduling_service.py:640-646`) discloses the blindness verbatim: *"It is NOT the delivery's fate — there is no delivery-completed callback (the adapter observes enqueues, never completions), so the field keeps reading `deferred` until the next DIRECT dispatch"* and *"`triggered` is the named TELL for a crash between the start row and the complete row (the only OPEN state)."* The status state machine **already has an open/terminal seam** — the job-queue delivery path simply never closes it. And the linkage column the Emperor's audit found empty **already exists**: `models.py:170 instance_id: str | None = Field(default=None, index=True)` — the contract is to **populate** it, not to add schema.

### A.1 The EXECUTION RECEIPT (field-level contract)

Every **fired** schedule must leave, on its `schedule_executions` row, a receipt with at least:

| Field | Contract |
|---|---|
| `triggered_at` | Exists today. The fire instant. |
| `instance_id` | **Non-NULL once a turn starts** (the column exists, `models.py:170`, indexed). NULL is permitted ONLY in the explicit never-started / failed-to-dispatch states, and MUST be accompanied by a status that says so. A completed fire with NULL `instance_id` is a contract violation. |
| `status` | **Terminal and truthful.** A closed vocabulary that at minimum distinguishes: `executed` (a turn ran to a terminal outcome — carry the outcome or a pointer to it) · `dispatched` (a turn started; outcome pending — the only OPEN state, today's `triggered`) · `never-started` (fired; no turn ever began — zero checkpoints, zero task/message rows, zero children) · `failed-to-dispatch` (the enqueue/claim path failed) · `suppressed` (operator intent won — mirrors the re-arm's `skipped_operator`). **`queued` is a transient dispatch-phase state only; it must never be the last thing an operator reads** for a completed or dead fire. The closing write is the missing **delivery-completed callback** the code itself names (`scheduling_service.py:640-643`) — the seam exists; the job-queue path must drive it. |
| `task_id` / `job_item_id` | **The durable handle** to the spawned Task/JobItem (shape precedent: the cancel response's `last_execution_id` echo, `routers/schedules.py:326`). This is what lets a supervisor FIND a pending task on a live instance instead of answering `no_active_job`. |

### A.2 Surface rules (extends §3.3 to both directions)

The receipt renders on **every surface** (tool list, REST list, GET): `last_run_at` (exists) · `last_execution_status` (truthful per A.1) · `last_execution_instance_id` (nullable only with an explicit never-started / failed-to-dispatch status) · the task/job handle. **A fire whose turn never started must be visibly distinguishable from a completed one at a glance** — the same misread class as the weekly-read-as-one-shot, and the exact blindness that stranded work during tonight's outage recovery. The §3.3 floor therefore reads: items 1–5 (next-direction) **plus** A.1's receipt fields (last-direction).

### A.3 Pins (extend §4.4; mutation-verified, harness fixtures only)

- **Receipt pin:** a harness fire that completes leaves `status=executed` + non-NULL `instance_id` + task handle; a simulated never-started fire leaves `status=never-started`, NULL `instance_id`, and **the listing renders the distinction**.
- **Mutation legs:** turn the terminal-state write off ⇒ pin reds; a `queued`-forever row ⇒ pin reds; a completed fire with NULL `instance_id` ⇒ pin reds.

### A.4 Supervisor decidability (routing — the leader's lane, job d271db9d, owns the repair)

The receipt is **the input that makes the repair path decidable**:
- **Re-drive selection** = fires with `status ∈ {never-started, failed-to-dispatch}` (or `dispatched` stale past a deadline) ⇒ re-drive. Without the receipt, the supervisor cannot distinguish "nothing to do" from "work silently lost."
- **Task recovery** = for `dispatched`/`executed-pending` fires, the `task_id`/`job_item_id` handle + `instance_id` let the supervisor locate the pending task **on a live instance** and answer "found task X on instance Y" instead of `no_active_job`.
- The repair path must **consume this contract**; this ruling specifies the contract, not the repair (no implementation here).

### A.5 Scope

The receipt contract is **UNCONDITIONAL Phase 1** — it is operator truth, not a scheduling capability. It does not change Branch A/B, does not enlarge Phase 2's collapsed scope (§2.4/§4.6), and is orthogonal to the jitter question: whichever branch the platform probe selects, **fires must leave receipts**.
