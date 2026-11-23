# Design Sketch — job-watch-restart-persistence (decision input)

**Base:** `/home/nea/ensemble-src` @ `a1130e257a3c3bb3929455aec28cacafcbfaa1a7`
**Date:** 2026-11-23
**Commission:** job-watch-restart-persistence (Emperor decision input)
**Status:** READ-ONLY sketch. No code, config, or migration authored. House rule: every new SQLModel table ships its own additive `*.sql` under `daemon/migrations/versions/` (130 files today, zero exceptions).

---

## 1. Current state (one paragraph)

`job_watchers` is **already a DB table** (`daemon/repositories/job_queue/watcher_models.py:73-104`, columns `watch_id`, `job_id`, `instance_id`, `watch_events`, `created_at`) with a claim-first exactly-once CAS (`daemon/services/work_notifier.py:836-860`, `DELETE … RETURNING` before delivery) and a boot reconcile sweep (`daemon/services/job_queue_service.py:516` `reconcile_terminal_watches`, wired at `daemon/api.py:2916`). What is **not** persisted: the in-RAM delivery-path buffers (`manager._pending_injections` FIFO, `daemon/manager.py:1634`, RAM-only by design — "A restarted session starts fresh"), plus any boot reconciliation for targets that became terminal *during* downtime beyond the held-watcher reconcile above, plus replay semantics for a watcher revived after daemon restart, plus a general watch-row audit/history record (deferred-debt (a) — `stored_watches` is lane-scoped quarantine only, not history). The Option A quarantine table (`stored_watches`, `daemon/repositories/job_queue/stored_watch_models.py`) covers only usage-limit re-armed rows; ordinary registration, transport-kind consumption, and S15 compensation remain unrecorded. This sketch targets that remainder; it does **not** re-litigate the already-landed Option A machinery.

---

## 2. Table shape sketch (extend vs. new table)

House rule: new SQLModel table ⇒ own additive `*.sql` in `daemon/migrations/versions/`; brand-new tables also get `SQLModel.metadata.create_all` on PG (stored_watch precedent — no `_ensure_postgres_columns` mirror).

### Existing carriers

- `job_watchers` (`watcher_models.py:73-104`): `watch_id` PK, `job_id` (semantically work_id, FK deliberately removed, `20260627_000002_drop_job_watchers_fk.sql`), `instance_id` FK→instances, `watch_events` JSONB (default `ALL_WATCHABLE_EVENTS`, 10 transport-only; +`mission_terminal` opt-in via `ALL_MISSION_TERMINAL_WATCHABLE_EVENTS`), `created_at`. Unique `(job_id, instance_id)`. Indexes on `job_id`, `instance_id`.
- `stored_watches` (`stored_watch_models.py`): quarantine for Option A re-armed rows — `stored_watch_id`, `original_work_id`, `retry_work_id`, `watcher_instance_id`, `original_watch_events`, `mission_instance_id`, `work_instance_id`, `status` (armed/released/rehydrated), `created_at`/`updated_at`. Unique `(original_work_id, watcher_instance_id)`.

### Requested shape → mapping

| Requested field | Existing column (job_watchers) | Existing column (stored_watches) | Gap |
|---|---|---|---|
| watcher instance | `instance_id` (FK) | `watcher_instance_id` (FK) | covered |
| target mission/receipt | `job_id` (=work_id/receipt) | `original_work_id`/`retry_work_id` | **mission root not stored on job_watchers** (only via resolve) |
| events filter | `watch_events` JSONB | `original_watch_events` | covered |
| armed_at | `created_at` | `created_at` | covered |
| state | **none** — row existence IS state (DELETE-on-claim) | `status` (armed/released/rehydrated) | **no state column on job_watchers** |

### Recommendation: EXTEND, do not mint a new table

Add two columns to `job_watchers` rather than a third table:

- `mission_instance_id: str | None` (nullable; populated at arm time by walking `instances.parent_id` to the permanent-tree root — same linkage `stored_watches` already stores, `stored_watch_models.py` docstring). This is the GC's `evaluate_mission_live` anchor; walking from the worker would answer "terminal" prematurely (deferred-debt (c)).
- `state: str` default `"armed"` with values `armed` | `delivered` | `skipped` (terminal-target-at-boot) — **only** if we adopt mark-and-skip (§3); otherwise keep existence-as-state and rely on the CAS. **ASSUMPTION:** adding a state column to a DELETE-on-claim table is a hybrid; the cleaner shape is to keep `job_watchers` existence-as-state and put durable audit in a separate history table. Flag for Emperor.

Rationale: a third table (`watch_history`) would duplicate the quarantine surface and re-open the three-writer contention the Option A matrix depth-3 ruling deliberately avoided (`stored_watch_repository.py` docstring: "NOT columns on job_watchers (three-writer contention)"). Extending `job_watchers` with one nullable mission link is the smallest additive change; the audit/history gap (deferred-debt (a)) is better served by riding the durable `JOB_COMPLETED` witness rows (EventBus lane) already shipped, not by a new history table, until ≥2 silent-loss families confirm.

**Migration shape (not authored here):** one additive `ALTER TABLE job_watchers ADD COLUMN mission_instance_id …` (+ optional `state`) `.sql` under `daemon/migrations/versions/`, PG via `create_all` for any brand-new table only. No new table ⇒ no new `*.sql` required for the column add; PG gets it via `_ensure_postgres_columns` (`daemon/manager.py`) — the same PG-evolution path as the dropped FK. **ASSUMPTION:** column-add via `_ensure_postgres_columns` is acceptable; if the Emperor wants a separate `*.sql` for audit parity, that is a one-file rider.

---

## 3. Restart semantics

**Boot re-hydration (already partially wired):** `reconcile_terminal_watches` (`job_queue_service.py:516`, called `api.py:2916`) walks every active watch, resolves the work (Task or JobItem via `_work_resolver`), and notifies if terminal, with the mission-live guard holding `mission_terminal` rows whose mission is still live. The `WatchReconcileSweepService` (`daemon/api.py:2003`) is the in-session backstop at 300s. **Gap:** no handling for watches whose target was terminal *during* downtime but whose `mission_instance_id` link is absent (pre-migration rows); and no handling for revived instances (send_message revive carve-out at `instance_messaging.py:2432`) re-arming dependency watchers. **ASSUMPTION:** revived-instance watcher re-arm is in scope here; verify against `instance_messaging.py` dependency-watcher re-arm path (not re-verified in this sketch).

**Targets already terminal at boot — RECOMMEND: deliver-once, not mark-and-skip.** Reasoning: the watcher prompt contract states "The FIRST `[JOB_EVENT]` after your watch is the signal; later events on the same mission's other receipts are echoes — act once" (`agents/_prompt_system/innate-skills/job-orchestration/skill.md:12`). Duplicates are therefore **tolerated by design**: an agent that receives a second `[JOB_EVENT]` for an already-acted mission ignores it (act once). Mark-and-skip would require a per-row `state` column + a second writer on the CAS path and would risk *losing* the single signal if the first delivery's envelope was dropped in transit. Deliver-once via the existing CAS (`claim_watchers_for_job_for_instances`) preserves exactly-once on the delivery side while the agent side absorbs echoes. **State this explicitly in the plan:** duplicate `[JOB_EVENT]` envelopes are an accepted, designed-for degradation, not a defect; the invariant is "at least one delivery reaches the watcher", not "exactly one envelope".

**Double-delivery reasoning:** the CAS `DELETE … RETURNING` (`work_notifier.py:836-860`) makes the DB side exactly-once; the agent-side "act once" makes the semantic side idempotent. A boot reconcile that re-delivers to an already-claimed row finds zero rows (CAS loser) and stands down — so a restart cannot double-fire the DB claim. The only double-fire window is two envelopes for the *same* mission across different receipts, which the prompt contract already absorbs. **No new dedup needed.**

---

## 4. TTL / cleanup — which sweep to ride

Verified cadences and wiring (all in `daemon/api.py` lifespan):

| Service | Cadence | Wiring site | First-tick | Notes |
|---|---|---|---|---|
| `WatchReconcileSweepService` | 300s default (`DEFAULT_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS`), `ServicesConfig.watch_reconcile_sweep_interval_seconds` | `daemon/api.py:2003` | immediate (`_run` calls `sweep_once()` first) | held-watcher reconcile + zombie-GC; CAS exactly-once; **best fit for watcher-instance-terminal reap + age TTL** |
| `MissionStrandHealer` | 300s default | `daemon/api.py:2038` | immediate | strand-only; terminalizes orphan rows; **not** a fit (different invariant: never-dispatched ghosts) |
| `SourcesGcService` | configurable, `sources_gc_interval_seconds` | `daemon/api.py:2124` | immediate | archives+deletes config rows; predicate at repo layer; **not** a fit (wrong domain) |

**Recommendation: ride `WatchReconcileSweepService`** (extend `reconcile_held_watches_for_instance` with two legs), not a new service:

1. **Watcher-instance-terminal reap:** when `instances.status` is terminal (completed/terminated/failed/error) for a watcher's `instance_id`, delete all its `job_watchers` rows + any `armed` `stored_watches` rows. This mirrors the existing zombie-GC leg (`remove_all_watches_for_job`) but keyed on the watcher, not the job. **ASSUMPTION:** instance-terminal detection reuses `instance_repository` listing; verify the exact query method (not re-verified here).
2. **Age-based TTL:** rows older than a configurable window (recommend reusing `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` = 6h, `mission_live_guard.py:221`, for consistency with the stored-watch GC) with a terminal or unresolvable target are reaped. Already partially implemented in the zombie-GC leg; extend the cutoff to cover `job_watchers` rows whose `job_id` no longer resolves.

**Do NOT ride `MissionStrandHealer`** — its invariant is strand-signature-only (never-dispatched ghosts with terminal direct parents); watcher reap has no strand signature. **Do NOT ride `SourcesGcService`** — different predicate domain and archive lifecycle.

**Orphaned-row hazard post-reap (BLAST RADIUS):** reaping a watcher row while a `[JOB_EVENT]` envelope is in-flight to that instance leaves a dangling notification target. Mitigation: reap is DELETE-on-claim-style (CAS) so an in-flight delivery that already claimed the row wins; the reap only removes unclaimed rows. A reap that races a live delivery is safe because the CAS serializes them. **ASSUMPTION:** the reap path must use the same CAS (`claim_watchers_for_job_for_instances` or a watcher-keyed equivalent) rather than a raw DELETE; verify the repository method exists (not re-verified).

---

## 5. Blast radius

- **Migration:** additive only — one nullable column on `job_watchers` (and optionally `state`); no new table, no data backfill required (NULL = unknown mission link; reconcile treats NULL as "walk at notify time", current behavior). **No frozen contract touched:** C1 buckets (`work_notifier.py:773-803`), M2 opt-in default (`watcher_models.py:34-62`), U1 sweep all unchanged. Frozen-contract exposure: none (A/E-class, per options-matrix §2(iv)).
- **Seams changed:** `job_queue_service.reconcile_terminal_watches` (add mission-link walk + watcher-terminal reap leg); `WatchReconcileSweepService` (extend sweep_once); `watcher_repository` (add watcher-keyed reap + CAS). No change to `notify_work_watchers` CAS chokepoint.
- **Duplicate `[JOB_EVENT]` risk:** low and accepted (§3). A restart can produce one extra envelope per mission across receipts; the prompt contract absorbs it ("act once"). No regression in exactly-once DB claim.
- **Interaction with mission-resolver liveness vocabulary:** `evaluate_mission_live` (`mission_live_guard.py:327`) is the canonical guard; the boot reconcile already consults it for `mission_terminal` rows (`job_queue_service.py:580-600`). The new mission-link column makes that consult cheaper (no resolve walk) but does not change semantics. `evaluate_mission_terminal` semantics (referenced in the brief) — **ASSUMPTION:** I could not locate a function by that exact name; the canonical is `evaluate_mission_live` (returns `MissionLiveVerdict`). Treat "evaluate_mission_terminal" in the brief as the terminal verdict derived from `evaluate_mission_live` (live=False + all-terminal). Flag for verification.
- **Max-50-watches cap:** `MAX_WATCHES_PER_INSTANCE = 50` (`daemon/tools/job_queue.py:104`), enforced at 4 arm sites (lines 1538, 2637, 2928, 4342, 4406). A restart that re-hydrates stale rows could push a revived instance over the cap and block new arms. **Mitigation:** the watcher-terminal reap (§4) must run *before* any re-arm on boot, and the cap check should treat `state='skipped'` (if adopted) rows as not counting. **Orphaned-row hazard post-reap:** if reap deletes rows that a still-live delivery expects, the delivery's CAS finds zero rows and stands down — safe, but the watcher never gets that envelope. Acceptable under the "at least one delivery" invariant; the next sweep re-evaluates. **ASSUMPTION:** cap enforcement reads `job_watchers` count via repository; verify the count query includes/excludes reaped rows (not re-verified).
- **RAM buffers (`_pending_injections`):** out of scope for this sketch's table work; they are RAM-only by design and survive neither restart nor persistence. If persistence of injected messages is wanted, that is a separate commission (checkpoint already persists the injected `HumanMessage` per `manager.py:1634` comment). Flag, do not design here.

---

## Open questions for Emperor

1. Adopt `mission_instance_id` column on `job_watchers` (recommended) or keep resolve-at-notify-time (current)?
2. Adopt a `state` column for mark-and-skip, or keep existence-as-state + deliver-once (recommended)?
3. Is revived-instance watcher re-arm in scope, or a separate commission?
4. Does "evaluate_mission_terminal" (brief) map to `evaluate_mission_live`-derived terminal, or is there a separate function I missed (ASSUMPTION)?
5. Should the audit/history gap (deferred-debt (a)) be closed now via durable `JOB_COMPLETED` witness rows, or deferred until ≥2 silent-loss families confirm?
