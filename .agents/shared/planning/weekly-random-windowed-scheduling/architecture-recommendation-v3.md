# Architecture Ruling v3 — weekly-random windowed scheduling

**Supersedes v2** (`architecture-recommendation-v2.md`); v1/v2 kept for audit trail (keep-both, newest-first). Design of record.

- **Date:** 2026-11-23 (~20:05Z) · **Lane:** architect (controller) · **Workers:** `ac129835` (source audit + follow-up), `43563213` (collision-probe census, in flight)
- **Evidence:** `origin/latest` @ `27f9130`; live release v0.31.42 = source commit `ea5325a` (ancestor of origin/latest; the release tree is a **frozen binary** — live code is read at its recorded `source_commit`). Every point below is identical in both trees.
- **What changed in v3:** (i) the "better" criterion is **widened from our-own-tasks collision to the host's recurring heavy work**, with the ruling restated as *jitter earns its complexity only against a MEASURED recurring collision*; (ii) the audit's named claims are answered explicitly — **is the chain path a supported capability or a seeded convenience?** (answer: neither — it is native-but-half-exposed, with consequences for the shape's size); (iii) the spawn/project_id finding is folded into the listing contract and routed.

---

## The short version (for the Emperor)

**Easier — no.** A fixed cron slot is one string that already re-arms, survives restarts and handles DST. Jitter needs pick + persist + re-pick + restart-safety + window edges.

**Better — the honest form:** "no" for our own tasks' sake (one task, one window — jitter changes *which minute*, not *how much*), and **yes only against a MEASURED recurring collision** with the PG host's own recurring heavy work (autovacuum on the toasted relation, Longhorn snapshot/backup schedules on the PVC, PG maintenance jobs, and the house's own recurring jobs). **That measurement has not been taken.** Until it is, the default posture is the fixed slot, and jitter is refused as unearned complexity — the same discipline that killed the compaction-tier build in one sentence. If the measurement shows a real collision, jitter becomes the remedy and the window field earns its build.

**The feature exists** — as native daemon code written by the maintenance system's own boot seeder, with a proven self-re-arm — but it is **half-exposed**: nobody can create one outside that system, and the operator surface calls it a one-shot. The canonical shape is therefore **ONE user-facing mechanism**; the chain marker becomes internal; the *expensive* half (generic windowed creation + jitter) is conditional on the collision measurement, while the *cheap* half (telling the operator the truth) ships now.

---

## 0. Source check — settled

| Question | Verdict | Evidence |
|---|---|---|
| Does `weekly-random` exist? | **YES — native daemon code** | `maintenance_seed.py:135 CHAIN_MODE = "weekly-random"`; chain block assembled at :376-381 (re-arm refresh) and :422-427 (seed config); boot chain guard `manager.py:1248-1260` → `seed_central_maintenance_schedule`. Under `daemon/`, the only writers of a `chain` block are the seeder's two sites; the only reader is `scheduler.py:608`. |
| Who wrote the live chain row? | **The daemon's boot seeder** | The row's config key set `{agent, chain, instance_mode, local_time, message, project_id, recurrence, run_at, timezone}` matches `_chain_config` (`maintenance_seed.py` ~:405-432) **EXACTLY (9/9, no extras, none missing)**. Corroborated independently by the leader's provenance measurement: **no `creator_instance_id`** (agent-facing creates stamp one — `scheduling_service.py:822-824`; the REST sentinel is suppressed, :109-111) **and no `schedule` key** ⇒ not the agent-tool path. Two independent directions, same conclusion. |
| Does it re-arm? | **YES — and natively, inside the scheduler adapter** | `scheduler.py:888-948` `_handle_consumed_one_time`: "A CHAIN row … is the self-rescheduling weekly trigger … the completion callback that DISABLES a one-shot is SKIPPED for it and the row's `run_at` is rewritten". `_rearm_chain_row` (:950-1070) picks, persists, promotes status, mints a fresh idempotency key. Cancelled/paused ⇒ `skipped_operator`; failure ⇒ row left ARMED for the boot guard. |
| Its "weekly" semantics | **5–13-day effective interval** | `_rearm_chain_row` docstring (:950-973): next weekday-D occurrence counted from the re-arm (`allow_today=False`) ⇒ "the effective interval is therefore 5–13 days rather than exactly 7 (BR-1 sub-semantics)". |
| Restart-safe? | **YES** | Persisted pick + stable key `scheduler:{source_id}:{run_at.isoformat()}` + partial UNIQUE index + phantom-row compensation. |
| Why did the listing lie? | **WRITE side stores `recurrence:"once"`; READ side has no window field** | `_row_to_detail` (:672-710) echoes `recurrence=config.get("recurrence")` verbatim, gates `cron_expression` on `recurrence=="cron"` (:698); `ScheduleDetail` (:194+) has no chain/window field. Identical in live `ea5325a`. |
| Was the next-fire dropped? | **One field, two sources** | Tool list computes it from the stored config (`_compute_next_run`, :468-520 — `run_at` branch precedes cron); the **REST list** derives `next_run_at` from the **live adapter's in-memory** `_get_next_trigger_time` (`routers/schedules.py:131-138`) — null when the adapter isn't registered. The surfaces can disagree ⇒ the misread class is structural, not a single missing field. |
| Does cancel free the name? | **NO** | `cancel_source_config` (`repository.py:604+`) is terminal, keeps `name` + all history; create-time uniqueness (`scheduling_service.py:736-742`) matches any row, dead or live. |

## 1. Is windowed-random EASIER? — **No.** (unchanged; one cron string + proven re-arm vs. pick + persist + re-pick + restart-safety + edge rules, with the house's own windowed implementation as the measured complexity bill — it still lies to its operator.)

## 2. Is it BETTER? — restated in the decision-relevant form

**2.1 The competitor set is NOT our scheduler tasks.** The reclaim lane's real competitors are the **PG host's own recurring heavy work** and the **storage/network path they share**: autovacuum on the toasted relation, Longhorn snapshot/backup schedules on the PVC, PG maintenance jobs (cron-driven vacuum/reindex/analyze), backup/monitoring agents, and the **house's own recurring jobs** on the same PG (checkpoint sweep, metrics collection, knowledge sweeps, the other scheduled lanes). A random pick inside 01:00–05:00 avoids those **only if they recur at fixed times** — which is precisely what is measurable.

**2.2 The ruling.** **Jitter earns its complexity only against a MEASURED recurring collision.** Two branches, and the default posture is explicit:
- **Branch A — no collision measured at the fixed slot ⇒ the fixed slot is STRICTLY better.** Simpler, predictable, receipt-friendly, aligned with the house's own "a mechanism must earn its complexity" discipline. **Jitter is refused; nothing is built.**
- **Branch B — a collision IS measured ⇒ jitter is the remedy.** Only then does window support become a first-class scheduler field (kill-switched jitter, default OFF), and only then does the re-arm/edge-rule machinery get built.
- **Status: unmeasured ⇒ Branch A.** The candidate fixed slot (Sunday 03:30 ICT = Saturday 20:30 UTC) already sits inside the desired 01:00–05:00 window; `central-maintenance-weekly-v2` stays armed on it, untouched.

**2.3 The measurement is a cheap read-only probe, not a build.** It asks one question — *what recurring work competes for this resource, and does the fixed slot collide with it?* — and is run by the **devops lane**, read-only, over one weekly observation cycle (autovacuum is threshold-driven rather than clock-driven, so attribution needs the cycle, not a snapshot; the repo-grounded recipe is being finalized by the collision census worker `43563213` and appended here when it lands). House discipline: **measure first, build never-but.**

## 3. Canonical shape — the audit's question answered explicitly

**3.1 Is the chain path a supported scheduling capability, or a seeded convenience? — NEITHER: it is native-but-half-exposed.**
- **Native:** the *scheduler adapter itself* understands `chain` and re-arms it — `scheduler.py:604-613` ("one concept, one marker … instead of a schedule name"), `:888-948` (the re-arm authority), `:950-1070` (`_rearm_chain_row`). This is not a convenience wrapper around the scheduler; the scheduler is what makes the chain self-rescheduling.
- **Maintenance-scoped creator:** only the boot seeder composes such rows; no user-facing creation path accepts a chain/window (create validation admits recurrence ∈ {once, daily, weekly, cron}).
- **Coupled internals:** the adapter imports the maintenance modules (`scheduler.py:107` `from …maintenance_seed import refresh_chain_keys`; `:1003` lazy import of `maintenance_schedule_knobs`) — the generic scheduler reaches into the maintenance system for its re-arm.
- **A surface that knows nothing about it:** `ScheduleDetail` has no window field; the listing renders a recurring chain task as `recurrence:"once"`.

**3.2 The ruled canonical shape.** **ONE user-facing mechanism:** `recurrence` (once | daily | weekly | cron) + local time + timezone, with an **optional `window`** field that exists as a first-class scheduler concept **only if Branch B fires**. The `chain` marker is demoted to an **internal compatibility representation** — it must never be a second user-visible mechanism. Consequences:
- **Cheap half (now):** the window is already IN the stored config (`chain.window_start/window_end/weekday/timezone`) — so the operator surface can show it **today, with no new scheduling machinery**. What must be built is the *truth-telling surface*, not a new capability.
- **Expensive half (conditional):** generic windowed creation, jitter, window-edge rules, and the re-arm proof leg — all gated on the measured collision.
- **If the maintenance system genuinely wants its 5–13-day spread after the chain marker retires:** the adapter already supports a native interval primitive (`scheduler.py:544`, `:569-574`, `:1130-1133` — `interval_seconds`; cron takes precedence when both are present). That is a one-line sanctioned alternative to the chain marker — **the spread does not justify a second scheduling mechanism.**

**3.3 The listing floor — non-negotiable, on EVERY surface:**
1. **recurrence truth** (a recurring chain task never renders as "once");
2. **window**, when the row has one (start, end, weekday, timezone);
3. **ACTUAL next-fire** from **one** authoritative source (the stored config) — non-null for an armed recurring row, identical on tool and REST surfaces;
4. **last-fire** and **status**;
5. **project scope** — the row's `project_id` is visible, because a listing that omits the scope is the same misread class (see §7).

## 4. Implementation spec (binding for the builder lane)

### Phase 1 — BUILD NOW (truth-telling surface; no new scheduling capability)
- **4.1 Listing truth.** `_row_to_detail` (`scheduling_service.py:672-710`): derive the effective recurrence when a `chain` block is present (never echo the stored "once" as the only truth); add a `window` field to `ScheduleDetail` (:194+) echoed from the chain block; **make the next-fire authoritative from the stored config on the REST list too** (replace/repair the adapter-derived `next_run_at`, `routers/schedules.py:131-138`) so one field has one source on both surfaces; surface `project_id` in the listing contract.
- **4.2 Namespace.** Scope create-time label uniqueness to **live** rows only (`scheduling_service.py:736-742`). **And — the hazard this creates:** the cancel/update-by-label path resolves "first match wins" (`tools/scheduling.py`, cancel docstring), so with dead names reusable a **label must resolve to the LIVE row** (prefer live; refuse ambiguity between two live rows). Dead rows keep their names for history.
- **4.3 Seed writes canonical rows going forward.** The maintenance seed stops composing chain-shaped rows for NEW schedules and writes a canonical recurring row (`recurrence=weekly` + cron + local_time + timezone) under a **distinct label** (avoids the reuse collision entirely); the archive-aware boot guard keeps its rule that an archived CANCELLED chain is never revived, so the cancelled chain row stays dead and is not resurrected. The chain marker remains readable for legacy rows (compatibility), and the adapter's chain re-arm remains until the last chain row is retired.
- **4.4 Pins (mutation-verified).** Listing pin: a chain-shaped fixture renders derived recurrence + window + a **non-null** next-fire + project scope on **both** surfaces; mutate the derivation out, or reintroduce an adapter-only next-fire source ⇒ pin reds. Namespace pin: create with a cancelled row's label succeeds; with a live row's label fails; label resolution prefers the live row. Seed pin: a fresh seed writes a canonical row with no `chain` block and a distinct label.

### Phase 2 — BUILD ONLY IF BRANCH B FIRES (a measured recurring collision)
- **4.5 Window as a first-class scheduler field** on weekly/daily in `create_schedule` + `task_schedule`, with **strict weekly-on-weekday semantics** — never the maintenance chain's 5–13-day drift; if interval spread is wanted, use the adapter's native `interval_seconds` primitive.
- **4.6 Jitter kill-switch** `ENSEMBLE_SCHEDULING_WINDOW_JITTER`, default OFF ⇒ next-fire = `window_start`.
- **4.7 Window-edge rules.** Reject `window_start == window_end`; reject sub-granularity windows; DST-gap-crossing windows go through the `tz.py` re-anchoring path with a loud `tz_warning`.
- **4.8 The re-arm proof leg.** Harness: fire one windowed cycle ⇒ assert a NEW persisted next-fire, same weekday, inside the window, status RUNNING, new idempotency key; mutate the re-arm out ⇒ leg reds.
- **4.9 The collision measurement itself** — spec in §2.3; devops lane, read-only, one weekly cycle. **Pass/fail criteria are appended by the census worker when it lands.**

## 5. Migration + namespace (PROPOSED — do NOT execute)

- **`central-maintenance-weekly-v2` stays armed on the proven cron** (Sunday 03:30 ICT). Nothing to migrate; the safety net is untouched.
- **The cancelled chain row (`central-maintenance-weekly`, 067cdc8d) stays cancelled.** The archive-aware boot guard never revives an archived CANCELLED chain; §4.3 must preserve that rule, and the new canonical seed row uses a **distinct label** so history stays greppable and the reuse collision never arises.
- **If/when Branch B fires and a windowed weekly is built:** convert v2 with a **dual-armed cutover** — the cron row stays armed until the FIRST windowed fire produces its receipt.

## 6. Guideline obligation — owed NOW

The chain machinery is exactly the house's "silently-biting rules" class. The guideline must state: chain-mode `weekly-random` exists and is native (the adapter re-arms it), but only the maintenance boot seeder creates one (no `creator_instance_id` stamp — agent-facing creates always carry one); its effective interval is 5–13 days, not 7; it is never disabled by a fire; until §4.1 lands, listings render it as `recurrence:"once"` with no window — read the REST config echo or the row's `chain` block for truth; cancelling is terminal and **keeps the name** until §4.2 lands. POK entries are filed (`sched-chain-weekly-random-maintenance-only`, `sched-cancel-keeps-name`); the human-facing guideline goes through the critical-note lane.

## 7. Cross-lane finding (evidence only — NOT taken here; leader's lane owns the fix)

The scheduler's **spawn path dropped the schedule's `project_id`**: a scheduled lane came up in the SYSTEM DEFAULT project and its own context opened "Non-Scoped Mode — Project Selection Required"; that lane's task then sat `pending`/unclaimed with its message `ready` and its agent loop never starting. This lane traced the writer / re-arm / listing, **not** the spawn-claim path. **Routing invariants for the owning lane:** (a) a schedule row's `project_id` MUST be honored by the spawn path — a spawned task inherits the row's project, never a substituted default; (b) the operator listing MUST show the project scope (§3.3 item 5) — a listing that omits scope is the same misread class; (c) a spawned task must not be left `pending`/unclaimed with its loop never started. **Flagged to the leader for routing to job d271db9d; not taken here.**

## Gaps / degraded / confidence

- `w-tradeoff` ran **DEGRADED — skill bank miss (`trade-off-analysis`)**; this bank has no design-dimension skills at all (the standing `structural-design` gap, confirmed broader). Method inline; overlap claims independently confirmed.
- The **collision measurement itself has not been run** — it is the named, cheap, read-only follow-up that decides Branch A vs Branch B (§2.3, §4.9). Its repo-grounded recipe is being finalized by `43563213` and appended here on landing.
- The operator's exact null next-fire was not reproduced; what is reproduced is the **two-source divergence class** (§0).
- Immaterial drift: the knobs module is at `daemon/services/maintenance_schedule_knobs.py` (`:260`), not under `repositories/source/`.
- **Confidence:** High on existence / writer / re-arm / listing / namespace / restart-safety (read directly in both trees). High on the canonical shape's *size* (the chain path is native-but-half-exposed ⇒ not a retire-and-forget, but not a unified model either). **Medium** on the Branch A/B outcome — that is precisely what the collision probe measures, and it is the only input that changes the build.

---

## CORRECTIONS (2026-11-23 ~20:20Z) — worker follow-up `ac129835` (source audit, Q6–Q10) + architect verification

**1. The live-tree check is VERIFIED, by a stronger route than source-grep.** The worker could not source-grep the release (correct: `releases/v0.31.42/daemon/` holds only `plugin_subsystem/`, and the daemon is a stripped ELF binary — no `scheduling_service.py`/`maintenance_seed.py` in the release). I closed it differently: the release's `manifest.json` records `source_commit = ea5325a60ed3c7c04b32a3eeeaa5dea83565bf26`, which is an **ancestor of `origin/latest`**, and I read the scheduler code at that commit — the chain writer (`maintenance_seed.py:135/376/422`), the creator-stamping rule, `_compute_next_run`'s `run_at`-first branch, and the listing mapping are **identical to origin/latest**. Live behaviour is therefore established by source-commit equivalence, not by grepping a binary. (Anchor refinement: the writer functions are `build_chain_config` (`maintenance_seed.py:390-432`) and `refresh_chain_keys` (:349-384), driven by `seed_central_maintenance_schedule` (:619).)

**2. The absent `creator_instance_id` stamp is CORROBORATING, NOT DISPOSITIVE.** The stamp is written only for agent-initiated creates (`scheduling_service.py:822-825`; the REST sentinel `REST_CALLER_ID` is suppressed, :109-111), so REST-created rows **and rows predating the capture feature** (the canonical cron rows of 2026-11-10/11-13) also lack it. The dispositive evidence that the chain row came from the maintenance seeder remains: **the exact 9/9 key-set match** with `build_chain_config` + `agent=maintenancer` + boot-time creation. The leader's provenance measurement is carried as corroboration, not as proof — §0's wording stands with that qualification.

**3. The operator's null next-fire is a LISTING-PATH artifact — the two surfaces genuinely disagree.** The **REST list** derives next-fire from the *registered adapter's in-memory* `_get_next_trigger_time()` (`routers/schedules.py:131-138`); a cancelled row is never started by the boot filter (`registry.py:292`, per `cancel_source_config`'s own docstring) ⇒ no adapter ⇒ `next_run_at = None`. The **service/tool path** computes from the stored config (`_compute_next_run`, `scheduling_service.py:468-520`) and *would* show the future `run_at`. ⇒ Not a missing computation — **one field with two sources on two surfaces**, which is exactly what §3.3 item 3 and §4.1 fix (single authoritative source, identical on both surfaces, non-null for an armed recurring row).

**4. §7 ROUTING CORRECTED — the defect's origin is the SEED, not the spawn propagation.** I previously wrote that "the spawn path dropped the schedule's `project_id`". The adapter does **not** drop it: it reads `self._project_id = scheduler_config.get("project_id")` (`scheduler.py:455`) and passes it into the durable enqueue (`scheduler.py:2966`, via `_route_via_job_queue`). The system-default project in the spawned lane originates **in the seeded row**: the seeder resolves `project_id = project_repo.ensure_system_default_project()` (`maintenance_seed.py:653`) and writes it into the config (`:416`) — so the row carries the default project and the spawn honors it faithfully. The owner-facing routing therefore becomes three items, all still **not taken here**:
  - **(a) the seed's project scope** — a seeded maintenance schedule should carry its own project, not the system default; that is the defect's origin.
  - **(b) a SECOND, genuinely unscoped path** — when `_project_id` is empty **and** `instance_mode` is `NEW_INSTANCE`, the scheduler falls through to `_execute_immediate` (`scheduler.py:2823-2832`) with **no job-queue and no project scoping at all** (the durable branch is taken only when a project id exists or the instance is reused). Two branches, two different incubations — the pending/unclaimed task symptom belongs to the job-queue branch.
  - **(c) the listing must show project scope** (§3.3 item 5) — a listing that omits scope is the same misread class.
  Ownership stays with the leader's lane (job d271db9d). This lane supplies evidence only.

---
**v4 ISSUED 2026-11-23 ~20:45Z** → `.agents/shared/planning/weekly-random-windowed-scheduling/architecture-recommendation-v4.md` — FINAL ruling of record (supersedes v3; v1–v3 kept, keep-both newest-first). v4 adds: the collision measurement is now SPECIFIED and cheap (platform Longhorn GET + house read-only SQL reuse + `systemctl list-timers` + `maintenance_runs` history, one weekly cycle, with a ≥2-cycle recurrence attribution rule and explicit pass/fail criteria); the competitor census (autovacuum threshold-driven 0.05/1000, vacuum sweep IDLE-gated, daemon jobs interval+IDLE-gated ⇒ only clock-pinned work can be avoided by a random pick); the ADJUDICATION of the "already jittered" precedent (the jittered chain row is the one cancelled tonight and replaced by the fixed cron — the precedent teaches operability, not new machinery, and it COLLAPSES Phase 2 to "re-enable the native chain with its two fixes", not "build a generic windowed capability"); and the explicit statement that a general-purpose user-facing windowed scheduling capability is DEFERRED, not ruled for.
