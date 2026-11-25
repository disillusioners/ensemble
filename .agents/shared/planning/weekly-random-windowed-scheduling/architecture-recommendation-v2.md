# Architecture Ruling v2 — weekly-random windowed scheduling

**Supersedes v1** (`architecture-recommendation.md`, corrections appended there). Design of record.

- **Date:** 2026-11-23 (~19:50Z)
- **Lane:** architect (controller). Workers: `ac129835` (source audit; follow-up corroboration in flight), `0a236c80` (approach comparison — **DEGRADED: skill-bank miss `trade-off-analysis`**, method inline)
- **Evidence trees:** `origin/latest` @ `27f9130096904c2111fa90fe9cca0be8d97867f4`; **live release v0.31.42 = source commit `ea5325a60ed3c7c04b32a3eeeaa5dea83565bf26`** (an ancestor of `origin/latest`). The live release tree is a **frozen binary** (`ensemble-prod`, 83 MB; `daemon/` holds only plugin helpers), so live code was read at its recorded `source_commit`. Every point this ruling turns on is **identical in both trees**.
- **No parallel lane ever ran.** The sonic-leader commission (job 2a666c3f) was cancelled at 18:47Z — PENDING, no instance, no work. Nothing here is attributed to it. The verdict rests on this lane's own worker plus the architect's own reads.

---

## The short version (for the Emperor)

Your premise is **half right**. Random-in-window is **not easier** — a fixed cron slot is one string that already re-arms, survives restarts and handles DST; jitter adds pick + persist + re-pick + restart-safety + window edges, and the one windowed implementation this house already shipped still misleads its own operator listing. It is **better in exactly one narrow sense** — spreading load when several heavy tasks share a window — and this lane has **one** task in its own four-hour window, so jitter changes *which minute* it runs, not *how much* work lands. And the feature **already exists** — as native daemon code, written by the maintenance system's own boot seeder, with a proven self-re-arm — but nobody outside that system can create one, and the operator listing calls it a one-shot. The proven cron weekly (Sunday 03:30 ICT) already sits inside your 01:00–05:00 window. **Ruling: keep v2 on cron; fix the listing so a weekly task can never read as a one-shot; stop dead labels from blocking names; build the generic windowed shape only when a second windowed consumer exists.**

---

## 0. Source check — the writer is identified, the fork is closed

| Question | Verdict | Evidence |
|---|---|---|
| Does weekly-random exist? | **YES — native daemon code, maintenance-scoped** | `daemon/repositories/source/maintenance_seed.py:135 CHAIN_MODE = "weekly-random"`; the chain block is assembled at :376-381 (re-arm refresh) and :422-427 (seed config). The seeder is invoked by the **boot chain guard** (`daemon/manager.py:1248-1260` → `seed_central_maintenance_schedule`). Under `daemon/`, the ONLY writers of a `chain` block are those two seeder sites; the only reader is `daemon/sources/adapters/scheduler.py:608`. |
| **Who wrote the chain row?** (the fork) | **The daemon's own boot seeder — resolved** | The row's config key set `{agent, chain, instance_mode, local_time, message, project_id, recurrence, run_at, timezone}` matches **EXACTLY (9/9, no extras, none missing)** the config built by `_chain_config` (`maintenance_seed.py` ~:405-432): `agent`←"maintenancer", `message`←`WEEKLY_RUN_MESSAGE`, `timezone`, `recurrence`←**"once"**, `local_time`←picked, `run_at`←`picked.run_at_iso`, `instance_mode`←"new_instance", `project_id`, + `chain`. ⇒ **NOT a hand-composed one-shot, NOT a direct lane write** — it is native daemon output, created by a boot at 2026-11-22T23:44:45Z. |
| Provenance cross-check | **CONFIRMED in code** | `creator_instance_id` is stamped only on agent-initiated creates (`scheduling_service.py:822-824`), and the `REST_CALLER_ID` sentinel is suppressed so REST-created rows carry none (:109-111, :820-824). The seeder stamps none. Absent creator ⇒ daemon/REST-written — consistent with the seeder writing the chain row and with the pre-existing canonical cron rows. |
| Is it actually "weekly"? | **NO — effective interval 5–13 days** | `_rearm_chain_row` docstring (`scheduler.py:950-973`): the next slot is the next weekday-D occurrence counted from the re-arm (`allow_today=False`), "the effective interval is therefore 5–13 days rather than exactly 7 (BR-1 sub-semantics)". |
| Does it re-arm after firing? | **YES — proven in code** | `scheduler.py:888-948` (`_handle_consumed_one_time`) skips the one-shot disable for chain rows and calls `_rearm_chain_row` (`:916`, `:950-1070`): next pick computed, `run_at`+`chain.run_at_utc` rewritten, status promoted to RUNNING, fresh idempotency key. The row is never disabled, never deleted. Cancelled/paused ⇒ `skipped_operator` (nothing written); re-arm failure ⇒ row left ARMED for the boot guard / past-due auto-fire. The boot guard is archive-aware: an archived CANCELLED chain is never revived. |
| Survives a daemon restart mid-cycle? | **YES** | The pick is persisted in the row; the idempotency key `scheduler:{source_id}:{run_at.isoformat()}` derives from the persisted value; the partial UNIQUE index collapses cross-restart duplicates; phantom rows compensated. |
| Why did the listing say `recurrence: once` / `cron_expression: null`? | **Two-sided defect, confirmed** | WRITE side: the seeder stores top-level `recurrence:"once"` (weekly-ness lives only in the `chain` block). READ side: `_row_to_detail` (`scheduling_service.py:672-710`) echoes `recurrence=config.get("recurrence")` verbatim and gates `cron_expression` on `recurrence == "cron"` (:698); `ScheduleDetail` (:194+) has **no window/chain field**. Identical in the live commit. |
| Was the real next-fire dropped? | **The label was; the next-fire is unreliable by construction** | `_compute_next_run` (:468-520) computes the next run from a future `run_at` — the `run_at` branch precedes the cron branch — so the **tool list** (`task_schedule_list` → `_row_to_detail`) *would* show `next_run_at_utc` for a live chain row. But the **REST list** derives `next_run_at` from the **live adapter's in-memory** `_get_next_trigger_time()` (`routers/schedules.py:131-138`) — a *different source for the same field*, null whenever the adapter is not registered. Two surfaces, two sources, one field ⇒ they can disagree and one can show null. The label misread is reproduced from code; the null next-fire is reproduced as a **divergence class**, not as a single field's absence. |
| Does cancel free the name? | **NO** | `cancel_source_config` (`repositories/source/repository.py:604+`) is terminal, atomic, keeps `name` and all `schedule_executions` history; create-time uniqueness (`scheduling_service.py:736-742` via `get_source_config_by_name`) matches **any** row, dead or live — a cancelled label blocks reuse until purged. Cancelled rows are hidden by default in listings and carry `status` + `cancelled_at`. |

## 1. Is windowed-random EASIER? — **No.**

A fixed slot is one cron string on machinery that already re-arms (13-fire daily, 2-fire weekly histories), already handles DST (`daemon/util/tz.py` re-anchoring), and is restart-safe for free. The windowed version needs: random pick → persist → fire → re-pick → restart-safety → window-edge rules (DST-crossing windows, `window_start == window_end`, sub-granularity windows) → a harder verification story. The bill is already measured: the maintenance implementation of exactly this pattern spans the seeder, the knobs module, a ~120-line re-arm seam and BR guards — **and its listing still misleads the operator.** That is what "harder" looks like in practice.

## 2. Is it BETTER? — **No here; yes in one narrow sense that does not apply today.**

- **The narrow win:** load-spreading when ≥2 heavy tasks share a window, or contend on the same clock minute as external I/O (Longhorn snapshots, backfills). With N=1 in its own four-hour window (240 candidate minutes, one consumer), jitter changes *which minute*, not *how many runs* — benefit ≈ nil.
- **The cost:** a maintenance system's deliverable is a receipt. "It didn't run" must be answerable at a glance; randomness spends predictability to buy nothing here.
- The proven canonical weekly (Sunday 03:30 ICT) **already sits inside** the desired Sunday 01:00–05:00 window — the intent is satisfied without jitter.
- **Flip condition (recorded):** the day ≥2 heavy windowed consumers exist, Scalability dominates and the unified windowed shape (item 3) becomes worth building.

## 3. Canonical shape (the ruling)

**ONE mechanism, not two.** `recurrence` ∈ {once, daily, weekly, cron} **+ optional `window`** {weekday, start, end, timezone} on recurring rows. The scheduler persists the concrete instant (`run_at` + window provenance) for windowed rows; a fixed slot needs nothing beyond the cron. The existing `chain` block is demoted to an internal/compatibility representation — never a second user-visible mechanism. Generic creation (tool/REST) gains a windowed option only in Phase 2.

**The floor — non-negotiable, on EVERY operator surface:**
1. the schedule's **recurrence truth** (never "once" for a recurring chain task);
2. its **window**, when it has one (start, end, weekday, timezone);
3. its **ACTUAL next-fire**, computed from **one** authoritative source (the stored config) and **non-null for an armed recurring row**;
4. its last-fire and status.
This is what makes the weekly-as-one-shot misread structurally impossible, on every surface, in both trees.

**Jitter is opt-in and kill-switched:** `ENSEMBLE_SCHEDULING_WINDOW_JITTER` (default OFF). Window + jitter OFF ⇒ fire at `window_start` (predictable, receipt-friendly). Window + jitter ON ⇒ random pick inside the window, persisted.

**Do not build the generic shape now.** No second consumer exists; N=1 buys nothing. Build Phase 1 (operator truth) now; Phase 2 waits behind the flip condition.

## 4. Implementation spec (binding for the builder lane)

### Phase 1 — BUILD NOW (operator truth; independent of any windowed machinery)
- **4.1 Listing truth.** `_row_to_detail` (`scheduling_service.py:672-710`): when a `chain` block is present, derive the effective recurrence label (e.g. `"weekly (windowed)"`) instead of echoing the stored `"once"`; add a `window` field to `ScheduleDetail` (:194+) echoed from the chain block; **make `next_run_at_utc` authoritative from the stored config on the REST list too** — replace the adapter-derived `next_run_at` (`routers/schedules.py:131-138`) with the service-computed value (or fall back to it when the adapter is absent), so one field has one source on both surfaces. Optionally the seeder stores `recurrence:"weekly"` + window going forward; read-side derivation still handles legacy rows (exactly one in the wild, cancelled).
- **4.2 Namespace.** Scope create-time label uniqueness to **live** rows only (skip `status='cancelled'` in the uniqueness lookup, `scheduling_service.py:736-742`). Dead rows keep their names for history; they no longer block reuse. Cancelled-hidden-by-default listing behavior stays.
- **4.3 Pins (mutation-verified).** Listing pin: a chain-shaped fixture renders derived recurrence + window + a **non-null** next-fire on **both** surfaces; mutating the derivation out (or reintroducing the adapter-only next-fire source) ⇒ pin reds. Namespace pin: create with a cancelled row's label succeeds; with a live row's label fails. Harness fixtures only — no live DB.

### Phase 2 — BUILD ONLY WHEN THE FLIP CONDITION FIRES (≥2 windowed consumers)
- **4.4 Generic windowed recurrence.** Optional `window` on weekly/daily in `create_schedule` + `task_schedule`, with **strict weekly-on-weekday semantics** — NOT the maintenance chain's 5–13-day drift (that semantic stays maintenance-internal; if ever exposed it ships as its own labeled mode, never under the word "weekly").
- **4.5 Re-arm.** Generalize `_rearm_chain_row`: compute next pick after fire, persist (`run_at` full-replace read-modify-write), promote status, mint a fresh idempotency key; on failure leave the row ARMED (copy `scheduler.py:950-973`). Boot reads the persisted `run_at` only — never re-picks, never double-fires.
- **4.6 Window-edge rules.** Reject `window_start == window_end`; reject windows shorter than pick granularity; windows crossing a DST-gap date go through the `tz.py` re-anchoring path with a loud `tz_warning`.
- **4.7 Kill-switch.** `ENSEMBLE_SCHEDULING_WINDOW_JITTER`, default OFF ⇒ next-fire = `window_start`.
- **4.8 The re-arm proof leg.** Harness: fire one windowed cycle ⇒ assert a NEW persisted `run_at`, same weekday, inside the window, status RUNNING, new idempotency key; mutate the re-arm out ⇒ leg reds.

## 5. Migration + namespace (PROPOSED — do NOT execute)

- **`central-maintenance-weekly-v2` stays on the proven cron** (Sunday 03:30 ICT — already inside 01:00–05:00). No migration is needed to satisfy the intent; the safety net stays armed and untouched.
- **If Phase 2 ever lands:** convert v2 to `weekly + window(Sun 01:00–05:00 ICT)` with dual-armed cutover — the cron row stays armed until the FIRST windowed fire produces its receipt.
- **Dead label `central-maintenance-weekly`:** after 4.2, leave it in place (hidden by default, history intact, greppable by status). Reclaiming the name is cosmetic; history readability wins.

## 6. Guideline obligation — owed NOW, not at Phase 2

The chain machinery is exactly the house's "silently-biting rules" class. The guideline must state: chain-mode `weekly-random` exists, is **maintenance-system-only** (written by the boot seeder; `task_schedule`/REST cannot create it; it carries no `creator_instance_id`); it **self re-arms** with a 5–13-day effective interval and is never disabled; until 4.1 lands, listings render it as `recurrence:"once"` with no window — read the REST config echo or the row's `chain` block for truth; cancelling is terminal and **keeps the name** until 4.2 lands. Two POK entries are filed (`sched-chain-weekly-random-maintenance-only`, `sched-cancel-keeps-name`); the human-facing guideline goes through the critical-note lane.

## 7. Cross-lane finding (evidence only — NOT taken here)

The leader measured a scheduled lane spawning under the **SYSTEM DEFAULT project** instead of the schedule row's own project, with its task left pending/unclaimed and its agent loop never starting. This lane traced the writer / re-arm / listing, **not** the spawn-claim path; the defect is owned by the leader's lane (job d271db9d). **Routing invariants for whoever fixes it:** (a) a schedule row's `project_id` MUST be honored by the spawn path — a spawned task inherits the row's project, never a substituted default; (b) the operator listing must show recurrence + window + ACTUAL next-fire so the misread is structurally impossible. A worker corroboration pass on the spawn path is in flight; evidence will be appended here when it lands.

## Gaps / degraded

- `w-tradeoff` ran **DEGRADED — skill bank miss (`trade-off-analysis`)**; this project's bank carries **no design-dimension skills at all** (the standing `structural-design` gap, confirmed broader). Method was embedded inline; its overlap claims were independently confirmed.
- The worker's follow-up corroboration pass (live-tree writer census, `_compute_next_run` semantics, creator stamping, spawn path) was **in flight** at issue time; every point it covers was independently established by the architect's own reads above, so the ruling does not wait on it.
- The operator's exact null next-fire was **not reproduced**; what is reproduced is the two-source divergence class (§0) — the label misread is exact, the next-fire unreliability is structural.
- Immaterial drift noted: the knobs module lives at `daemon/services/maintenance_schedule_knobs.py` (`:260` `pick_next_fire_instant`), not under `repositories/source/`.

## Confidence

**High** on existence / writer / re-arm / listing / namespace / restart-safety — all read directly in both trees (`origin/latest` `27f9130` and live `ea5325a`). **Medium-high** on the trade-off (the N=1 arithmetic is firm; the future consumer count is an assumption). **The single assumption that would flip the ruling:** a second heavy windowed consumer appearing ⇒ Phase 2 fires and the unified windowed shape becomes the right build, exactly as specified in 4.4–4.8.

---
**v3 ISSUED 2026-11-23 ~20:05Z** → `.agents/shared/planning/weekly-random-windowed-scheduling/architecture-recommendation-v3.md` — supersedes v2 (keep-both, newest-first). v3 carries: (i) the WIDENED "better" criterion — jitter earns its complexity only against a **MEASURED recurring collision** with the PG host's own heavy work (Branch A = no collision ⇒ fixed slot strictly better, jitter refused; Branch B = collision measured ⇒ jitter is the remedy), with the measurement named as a cheap read-only devops probe; (ii) the explicit answer to the audit's question — the chain path is **native-but-half-exposed** (the scheduler adapter itself re-arms it; only the maintenance boot seeder creates one; the operator surface knows nothing about it), so the canonical shape is ONE user-facing mechanism with the chain marker demoted to internal, the window exposed on the listing for free today, and generic windowed creation + jitter gated on the collision measurement; (iii) the spawn/project_id finding folded into the listing contract and routed to the leader's lane.
