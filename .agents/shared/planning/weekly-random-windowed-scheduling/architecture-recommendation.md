# Architecture Ruling — weekly-random windowed scheduling

- **Date:** 2026-11-23
- **Architect instance:** this lane (controller); workers `ac129835` (source audit, skill `claim-verification-readonly-report`) + `0a236c80` (approach comparison, DEGRADED — no trade-off skill in bank, method inline)
- **Evidence tree:** `origin/latest` @ `27f9130096904c2111fa90fe9cca0be8d97867f4`, fresh detached worktree `/home/nea/ensemble-src-wt-sched-audit` (left standing for spot-checks)
- **Status:** BINDING — design of record for the held implementation lane (sonic-leader job 2a666c3f proceeds against this doc, not against its own draft)
- **Fences honored:** read-only on live schedules + live DB; nothing cancelled/created/paused/re-armed; the 19:00Z one-shot untouched; no promote; no contact with the outage-recovery lane (job d271db9d)

---

## The short version (for the Emperor)

Your premise is **half right**. Random-in-window is **not easier** — a fixed cron slot is one string that already works, re-arms, and survives restarts; the windowed version needs pick + persist + re-pick + restart-safety, and the one windowed implementation this house already shipped still misleads its own operator listing. It is **better in exactly one narrow sense** — spreading load when several heavy tasks share a window — and this lane has exactly **one** task in its own 4-hour window, so jitter changes *which minute* it runs, not *how much* work lands. Meanwhile the feature **already exists** in the code — but only inside the maintenance system, where nobody but the boot seeder can create it, and where its own listing labels it a one-shot. The proven cron weekly (`central-maintenance-weekly-v2`, Sunday 03:30 ICT) already sits inside the desired 01:00–05:00 window. **Ruling: keep v2 on cron; fix the listing so a weekly task can never read as a one-shot; fix name-blocking; build the generic windowed shape only when a second windowed consumer actually exists.**

---

## 0. Source-check verdict (checked first, as instructed)

Every claim below verified by this lane's own reads at tip `27f9130` (not taken from the parallel sonic audit):

| Question | Verdict | Evidence |
|---|---|---|
| Does weekly-random exist? | **YES — but maintenance-scoped only** | `daemon/repositories/source/maintenance_seed.py:135` `CHAIN_MODE = "weekly-random"`; rows created ONLY by the boot seeder (`manager.py:1248-1260` → `seed_central_maintenance_schedule`). The generic surface (`task_schedule` tool, REST) validates recurrence ∈ {once, daily, weekly, cron} — there is **no** window/chain option (`scheduling_service.py` create validation ~:736). |
| Is it actually "weekly"? | **NO — effective interval is 5–13 days** | `_rearm_chain_row` docstring (`scheduler.py:950-973`), verbatim: "the next slot is the next weekday-D occurrence counted from the re-arm (`allow_today=False`) … the effective interval is therefore 5–13 days rather than exactly 7 (BR-1 sub-semantics)". So even maintenance-scoped, this is *weekday-anchored interval-random*, not strict weekly. |
| Does it re-arm after firing? | **YES — proven in code** | Consume path `scheduler.py:888-948` (`_handle_consumed_one_time`) SKIPS the one-shot disable for chain rows and calls `_rearm_chain_row` (`:916`, `:950-1070`), which rewrites `run_at` + `chain.run_at_utc` to the next pick, promotes status to RUNNING, and mints a fresh idempotency key. The row is never disabled, never deleted. Cancelled/paused ⇒ `skipped_operator` (nothing written); re-arm failure ⇒ row left ARMED for the boot guard / past-due auto-fire. |
| Survives daemon restart mid-cycle? | **YES** | The pick is persisted in the row (`run_at` + `chain.run_at_utc`); idempotency key `scheduler:{source_id}:{run_at.isoformat()}` is derived from the persisted value, stable across boots; partial UNIQUE index collapses cross-restart duplicates; phantom rows compensated (audit: `job_queue_service.py:1473-1584`, `instance_messaging.py:3504-3650`). |
| Why does the listing show `recurrence: once`, `cron_expression: null`? | **Two-sided defect: a misleading stored label + a DTO with no window field** | WRITE side: the seeder stores top-level `recurrence:"once"` on chain rows (weekly-ness lives only in the `chain` block). READ side: `_row_to_detail` (`scheduling_service.py:672-710`) echoes `recurrence=config.get("recurrence")` verbatim and gates `cron_expression` on `recurrence == "cron"`; `ScheduleDetail` (`:194+`) has **no window/chain field at all** — confirmed by direct read of the model. |
| Is the true next-fire dropped too? | **NO (correction to the observed defect)** | `next_run_at_local/utc` are surfaced from the persisted `run_at` via `_compute_next_run` (`:699-701`). The real next-fire is NOT dropped for a live, future-picked row. The likely explanation for the observed null: the row was already **cancelled** at capture time. Only the *label* (recurrence) and the *window* are dropped. |
| Does cancel free the name? | **NO** | `cancel_source_config` (`repositories/source/repository.py:604+`) is terminal, atomic, keeps `name` and all `schedule_executions` history; create-time uniqueness (`scheduling_service.py:736-742` via `get_source_config_by_name`) matches **any** row, dead or live — a cancelled label blocks reuse until purged. Cancelled rows ARE hidden by default in listings and carry `status` + `cancelled_at`. |

---

## 1. Ruling: is windowed-random EASIER than a fixed cron slot?

**NO.** A fixed slot is one cron string on machinery that already re-arms (proven: 13-fire daily, 2-fire weekly histories), already handles DST (`daemon/util/tz.py` re-anchoring), and is restart-safe for free. The windowed version requires: random pick → persist → fire → re-pick → restart-safety → window-edge rules (DST-crossing windows, `window_start == window_end`, sub-granularity windows) → a harder verification story. The empirical bill is already on the table: the maintenance system's implementation of exactly this pattern spans a ~1353-line seeder, a knobs module, a ~120-line re-arm seam and BR guards — **and its listing still misleads the operator**. That is the complexity of windowed-random, measured, not estimated.

## 2. Ruling: is it BETTER?

**NO — for this lane. YES in one narrow sense, which does not apply today.**

- The narrow sense: load-spreading when **≥2 heavy tasks share a window** or contend on the same clock minute as external I/O (Longhorn snapshots, backfills). The trade-off worker quantified it honestly: for N=1 in its own 4-hour window (240 candidate minutes, one consumer), jitter changes *which minute*, not *how many runs* — benefit ≈ nil.
- The cost side is real: a maintenance system's deliverable is a receipt. "It didn't run" must be answerable at a glance; randomness spends predictability to buy nothing here.
- The proven canonical weekly (Sunday 03:30 ICT) **already sits inside** the desired Sunday 01:00–05:00 window. The intent is satisfied without jitter.
- **Flip condition (recorded):** the day ≥2 heavy windowed consumers exist, Scalability dominates and the unified windowed shape (item 3) becomes worth building.

## 3. Canonical shape (the ruling)

**ONE mechanism, not two.** `recurrence` ∈ {once, daily, weekly, cron} **+ optional `window`** {weekday, start, end, timezone} on recurring rows. The scheduler persists the concrete instant (`run_at`, plus window provenance) for windowed rows; a fixed slot needs no persistence beyond the cron. The existing `chain` block is demoted to an internal/compatibility representation — never a second user-visible mechanism.

**The floor (non-negotiable, whatever else any lane does):** on EVERY operator surface, a recurring task must render its recurrence truth, its window (if any), and its ACTUAL persisted next-fire — making it structurally impossible to read a weekly task as a one-shot. Today's defect violates this floor; item 4.1 fixes it.

**Jitter is opt-in and kill-switched:** `ENSEMBLE_SCHEDULING_WINDOW_JITTER` (default OFF). Window present + jitter OFF ⇒ fire at `window_start` (predictable, receipt-friendly). Window present + jitter ON ⇒ random pick inside the window, persisted. This gives the Emperor the load-spreading lever without spending predictability by default.

**Do not build the generic shape now.** No second consumer exists. Build Phase 1 (operator truth) now; hold Phase 2 behind the flip condition.

## 4. Implementation spec (binding for the builder lane)

### Phase 1 — BUILD NOW (small, independent, no windowed machinery required)

- **4.1 Listing truth.** `_row_to_detail` (`scheduling_service.py:672-710`): when a `chain` block is present, derive the effective recurrence label (e.g. `"weekly (windowed)"`) instead of echoing the stored `"once"`; add a `window` field to `ScheduleDetail` (`:194+`) echoed from the chain block; keep `next_run_at_utc`. Both surfaces (tool list via `ScheduleDetail.model_dump()`, REST) inherit the fix. Optionally, the seeder stores `recurrence:"weekly"` + window going forward — read-side derivation still handles legacy rows (there is exactly ONE in the wild, cancelled).
- **4.2 Namespace.** Scope create-time label uniqueness to **live** rows only (skip `status='cancelled'` in the uniqueness lookup, `scheduling_service.py:736-742`). Dead rows keep their names for history; they no longer block reuse. Cancelled-hidden-by-default listing behavior stays.
- **4.3 Pins (mutation-verified, per house standard).**
  - Listing pin: chain-shaped fixture → tool/REST output contains derived recurrence label + window + `next_run_at_utc`; mutate the derivation out ⇒ pin reds.
  - Namespace pin: create with a cancelled row's label succeeds; with a live row's label fails.
  - No live-DB contact; harness fixtures only.

### Phase 2 — BUILD ONLY WHEN THE FLIP CONDITION FIRES (≥2 windowed consumers)

- **4.4 Generic windowed recurrence.** Optional `window` on weekly/daily in `create_schedule` + `task_schedule`. **Strictly weekly-on-weekday semantics** — NOT the maintenance chain's 5–13-day drift (that semantic stays maintenance-internal; if ever exposed, it ships as its own labeled mode, never under the word "weekly").
- **4.5 Re-arm.** Generalize `_rearm_chain_row`: after fire, compute next pick, persist (`run_at` full-replace read-modify-write), promote status, fresh idempotency key. Re-arm failure leaves the row ARMED (copy the existing resilience semantic, `scheduler.py:950-973`). Boot reads the persisted `run_at` only — never re-picks, never double-fires (existing partial UNIQUE index holds).
- **4.6 Window-edge rules.** Reject `window_start == window_end`; reject windows shorter than pick granularity; windows crossing a DST-gap date go through the `tz.py` re-anchoring path with a loud `tz_warning`.
- **4.7 Kill-switch.** `ENSEMBLE_SCHEDULING_WINDOW_JITTER`, default OFF ⇒ next-fire = `window_start`.
- **4.8 The re-arm mutation leg (the proof the Emperor's question is really asking for).** In a harness: fire one windowed cycle → assert a NEW `run_at` persisted, same weekday, inside the window, status RUNNING, new idempotency key; mutate the re-arm out ⇒ leg reds.

## 5. Migration + namespace (PROPOSED — do NOT execute)

- **`central-maintenance-weekly-v2` stays on the proven cron** (Sunday 03:30 ICT — already inside 01:00–05:00). No migration is needed to satisfy the intent; the safety net stays armed, untouched.
- **If Phase 2 ever lands:** convert v2 to `weekly + window(Sun 01:00–05:00 ICT)` with dual-armed cutover — the cron row stays armed until the FIRST windowed fire produces its receipt. Same pattern the house already uses for boot-seeded safety nets.
- **Dead label `central-maintenance-weekly`:** after 4.2, leave it in place (hidden by default, history intact, greppable by status). Reclaiming the name is cosmetic; history readability wins.

## 6. Guideline obligation (the feature owes one — NOW, not at Phase 2)

The chain machinery is exactly the house's "silently-biting rules" class. A guideline must state: chain-mode `weekly-random` exists, is **maintenance-system-only** (boot-seeded; `task_schedule`/REST cannot create it); it **self re-arms** with a 5–13-day effective interval and is never disabled; until 4.1 lands, tool listings render it as `recurrence:"once"` — read the REST config echo or the row's `chain` block for truth; cancelling is terminal and **keeps the name** until 4.2 lands. Filing route: the leader's critical-note/guideline lane. This ruling's two POK entries cover the interim.

---

## Gaps / degraded

- `w-tradeoff` ran **DEGRADED — skill bank miss (`trade-off-analysis`)**; no design-dimension skill exists in this project's bank (matches the standing structural-design gap note). Method was embedded inline; its overlap claims were independently confirmed by the audit + my reads.
- One immaterial drift in the audit report: the knobs module lives at `daemon/services/maintenance_schedule_knobs.py` (`:260` `pick_next_fire_instant`), not under `repositories/source/`.
- The "listing showed no next-fire" observation is reconciled (cancelled-at-capture) but not provably so — Phase 1's listing pin makes the question moot.

## Confidence

**High** on existence / re-arm / listing / namespace / restart-safety — all directly read at tip `27f9130`. **Medium-high** on the trade-off (the N=1 judgment is arithmetic, but the future consumer count is an assumption). **The single assumption that would flip this ruling:** a second heavy, windowed consumer appearing — then Phase 2 fires and the unified windowed shape becomes the right build, exactly as specified in 4.4–4.8.

---

## CORRECTIONS (2026-11-23 ~19:40Z) — appended to the issued v1; a corrected v2 (`architecture-recommendation-v2.md`) supersedes this file once the in-flight live-tree check lands

1. **No parallel audit lane ever ran — nothing here is attributed to it.** The sonic-leader "weekly-random windowed scheduling" commission (job 2a666c3f) was CANCELLED at 18:47Z, still PENDING, with no instance and no work. Every verdict in this document rests on this lane's own worker (`ac129835`) plus the architect's own reads at tip `27f9130`. The Status line's phrase "the held implementation lane (sonic-leader job 2a666c3f proceeds against this doc)" is **withdrawn** — no such lane exists; this document is the design of record for whoever builds it.

2. **The writer of the chain row is now identified — the fork is CLOSED on the daemon's own boot-seeder.** The chain row's config key set `{agent, chain, instance_mode, local_time, message, project_id, recurrence, run_at, timezone}` matches **EXACTLY (9/9 — no extras, no missing)** the config assembled by `_chain_config` in `daemon/repositories/source/maintenance_seed.py` (~:405-432): `agent` ← "maintenancer", `message` ← `WEEKLY_RUN_MESSAGE`, `timezone`, `recurrence` ← default **"once"**, `local_time` ← picked, `run_at` ← `picked.run_at_iso`, `instance_mode` ← "new_instance", `project_id`, plus the `chain` block written at :419-430 (`mode`, `window_start`, `window_end`, `weekday`, `timezone`, `run_at_utc`). The seeder runs from the boot chain guard (`daemon/manager.py:1248-1260` → `seed_central_maintenance_schedule`). Under `daemon/`, the only WRITERS of a `chain` block are the seeder's two sites (seed :419-430, re-arm refresh `refresh_chain_keys` :373-384); the only READER is `daemon/sources/adapters/scheduler.py:608`. ⇒ **NOT a hand-composed one-shot, NOT a direct lane write — the row is native daemon output**, created by a boot at 2026-11-22T23:44:45Z. The feature IS native code: maintenance-scoped, agent-uncreatable.

3. **Creator-stamp semantics confirmed in code**: `creator_instance_id` is stamped only on agent-initiated creates (`scheduling_service.py:822-824`), and the `REST_CALLER_ID` sentinel is suppressed so REST-created rows carry none (:109-111, :820-824). Absent creator ⇒ REST- or daemon-written — consistent with the seeder writing the chain row and with the pre-existing canonical cron rows (also unstamped). The leader's provenance inference is CONFIRMED, and it independently corroborates item 2.

4. **The next-fire conflict is a LIVE-vs-ORIGIN tree question, not a code contradiction.** At `origin/latest`, `_compute_next_run` (`scheduling_service.py:468-520`) computes the next run from a FUTURE `run_at` — the `run_at` branch precedes the cron branch, and a future `run_at` with a resolvable timezone returns `(local, utc)` — so **at origin/latest a chain row WOULD show `next_run_at_utc`**. My v1 §0 row "the real next-fire is NOT dropped" was therefore correct for origin/latest but does not yet explain the operator's live view. The live-release-tree (v0.31.42) check is in flight; if the live release predates the `run_at` branch, the operator's null next-fire is explained by release drift, and the "listing read the wrong fields" defect is confirmed for the tree the Emperor actually ran. **The LISTING FLOOR is unchanged and now doubly load-bearing:** recurrence + window + ACTUAL next-fire, with a non-null next-fire for an armed recurring row, pinned.

5. **Cross-lane finding (evidence only — NOT taken here):** the leader measured a scheduled lane spawning under the SYSTEM DEFAULT project instead of the schedule row's own project, with its task left pending/unclaimed and its agent loop never starting. The scheduler's spawn/claim path has not been traced by this lane; the defect is owned by the leader's lane (job d271db9d). **Routing invariant for whoever fixes it:** a schedule row's `project_id` MUST be honored by the spawn path (a spawned task inherits the row's project, never a default substituted), and the listing must surface `recurrence` + `window` + ACTUAL next-fire so the misread is structurally impossible.

---
**v2 ISSUED 2026-11-23 ~19:50Z** → `.agents/shared/planning/weekly-random-windowed-scheduling/architecture-recommendation-v2.md` — this v1 file is superseded (kept for audit trail; keep-both newest-first). v2 carries: the closed writer fork (boot seeder, 9/9 key-set match), the live-release source-commit evidence (`ea5325a`), the two-source next-fire divergence finding, the strengthened listing floor, and the cross-lane spawn/project_id routing.
