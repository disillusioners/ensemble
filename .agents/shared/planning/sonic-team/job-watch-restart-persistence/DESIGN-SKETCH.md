# Design Sketch — job-watch-restart-persistence (decision input)

**Base:** commit `a1130e257a3c3bb3929455aec28cacafcbfaa1a7` (all anchors below resolved with `git show a1130e257a:<path>`, never from a working checkout).
**Date:** 2026-11-23
**Commission:** job-watch-restart-persistence (Emperor decision input)
**Status:** READ-ONLY sketch. No code, config, or migration authored. House rule: every new SQLModel table ships its own additive `*.sql` under `daemon/migrations/versions/` (137 files at base, zero exceptions).
**Checkout note:** the canonical checkout `/home/nea/ensemble-src` is DETACHED HEAD @ `bf4bfde6e` — it is never a commit target and never an anchor source; every anchor here is pinned to the base object `a1130e257a`.

---

## 1. Current state (one paragraph)

`job_watchers` is **already a DB table** (`daemon/repositories/job_queue/watcher_models.py:65-106` — `watch_id` 78, `job_id` 94, `instance_id` 97, `watch_events` 101, `created_at` 106) with a claim-first exactly-once CAS (service tier `daemon/services/work_notifier.py:947-958`; repo tier `daemon/repositories/job_queue/watcher_repository.py:441-452` — `DELETE … RETURNING`, committed before delivery) and a boot terminal-replay scan (`daemon/services/job_queue_service.py:516` `reconcile_terminal_watches`, wired at `daemon/api.py:3121`). **There is no in-memory watch store to re-hydrate** — watches are DB rows read live; the only boot-time watch action is that replay scan plus the sweeps' first tick. What is genuinely *not* persisted: the delivery-side RAM injection FIFO (`daemon/manager.py:1782` `_pending_injections`, RAM-only per the comment at `:1773-1781`), and a general watch-row audit/history record (deferred-debt (a) — `stored_watches` is lane-scoped quarantine only). The Option A quarantine table (`stored_watches`, `daemon/repositories/job_queue/stored_watch_models.py:68-128`) covers only usage-limit re-armed rows; ordinary registration, transport-kind consumption, and S15 compensation remain unrecorded. **"Terminal during downtime" is NOT a gap** — per REPORT-1b §2(ii) the daemon is the sole writer while down, so a work item cannot transition in that window; the only mutation possible "while down" is a hard reboot that killed the previous process mid-finalize, and that is covered by `JobRecoveryService.recover_on_startup()` + `StaleTaskRecovery` (REPORT-1b §2(iii)). This sketch targets the true remainder; it does **not** re-litigate landed Option A machinery.

---

## 2. Table shape sketch (extend vs. new table)

House rule: new SQLModel table ⇒ own additive `*.sql` in `daemon/migrations/versions/`; brand-new tables also get `SQLModel.metadata.create_all` on PG (stored-watch precedent — no `_ensure_postgres_columns` mirror).

### Existing carriers

- `job_watchers` (`watcher_models.py:65-106`): `watch_id` PK, `job_id` (semantically work_id; FK deliberately removed — `daemon/migrations/versions/20260627_000002_drop_job_watchers_fk.sql`), `instance_id` FK→instances, `watch_events` JSONB (default `ALL_WATCHABLE_EVENTS`, `:34-36`, 10 transport-only; `mission_terminal` opt-in via `ALL_MISSION_TERMINAL_WATCHABLE_EVENTS`, `:60-62`), `created_at`. Unique `(job_id, instance_id)`. Indexes on `job_id`, `instance_id`.
- `stored_watches` (`stored_watch_models.py:68-128`): quarantine for Option A re-armed rows — `stored_watch_id` 100, `original_work_id` 106, `retry_work_id` 107, `watcher_instance_id` 110, `original_watch_events` 115, `mission_instance_id` 118, `work_instance_id` 119, `status` 122, `created_at`/`updated_at` 127-128. Unique `(original_work_id, watcher_instance_id)`.

### Requested shape → mapping

| Requested field | `job_watchers` | `stored_watches` | Gap |
|---|---|---|---|
| watcher instance | `instance_id` (FK) | `watcher_instance_id` (FK) | covered |
| target mission/receipt | `job_id` (=work_id/receipt) | `original_work_id`/`retry_work_id` | **mission root not stored on `job_watchers`** (only via resolve) |
| events filter | `watch_events` JSONB | `original_watch_events` | covered |
| armed_at | `created_at` | `created_at` | covered |
| state | **none** — row existence IS state (DELETE-on-claim) | `status` (armed/released/rehydrated) | **no state column on `job_watchers`** |

### Recommendation: EXTEND, do not mint a new table

Add two columns to `job_watchers` rather than a third table:

- `mission_instance_id: str | None` (nullable; populated at arm time by walking `instances.parent_id` to the permanent-tree root — the same linkage `stored_watches` already stores, `stored_watch_models.py:118`). This is the GC's `evaluate_mission_live` anchor; walking from the worker would answer "terminal" prematurely (deferred-debt (c)).
- `state: str` default `"armed"` with values `armed` | `delivered` | `skipped` (terminal-target-at-boot) — **only** if mark-and-skip (§3) is adopted; otherwise keep existence-as-state and rely on the CAS. **ASSUMPTION:** a state column on a DELETE-on-claim table is a hybrid; the cleaner shape is to keep `job_watchers` existence-as-state and put durable audit in a separate history table. Flag for Emperor.

Rationale: a third table (`watch_history`) would duplicate the quarantine surface and re-open the three-writer contention the Option A matrix depth-3 ruling deliberately avoided — the rationale is stated verbatim in `daemon/repositories/job_queue/stored_watch_models.py:39-41` ("NOT columns on `job_watchers` (three-writer contention per the matrix depth-3 ruling) … Standalone table, own `.sql` migration"). Extending `job_watchers` with one nullable mission link is the smallest additive change; the audit/history gap (deferred-debt (a)) is better served by riding the durable `JOB_COMPLETED` witness rows (EventBus lane) already shipped, not a new history table, until ≥2 silent-loss families confirm.

**Migration shape (not authored here):** additive column(s) on `job_watchers`; PG picks them up via `_ensure_postgres_columns` (`daemon/manager.py:8936`) — the same PG-evolution path as the dropped FK. No brand-new table ⇒ no new `*.sql` needed; if the Emperor wants audit parity, one additive `*.sql` is a one-file rider. **ASSUMPTION:** column-add via `_ensure_postgres_columns` is acceptable; no schema/SQL is authored by this sketch.

---

## 3. Restart semantics

**Boot behaviour (already wired — no re-hydrate to add):** `reconcile_terminal_watches` (`job_queue_service.py:516`, called `api.py:3121`) walks every active watch (`watcher_repository.py:471` `get_all_active_watches`), resolves the work (Task or JobItem via `_work_resolver`), and notifies if terminal, with the mission-live guard holding `mission_terminal` rows whose mission is still live (`job_queue_service.py:585-601`). `WatchReconcileSweepService` (`api.py:2183`) is the in-session backstop at 300s, first tick immediate (`watch_reconcile_sweep.py:88`, `:400` sweeps before the `:424` sleep); it also runs the stored-watch GC leg (`watch_reconcile_sweep.py:294` → `reconcile_stored_watches`). Per REPORT-1b §2(ii) there is **no "terminal during downtime" class** to cover, and per REPORT-1b §1.7 there is **no RAM watch state** to rebuild. The only genuine open items are: rows whose `mission_instance_id` is absent (pre-migration rows, once §2 is adopted) and revived-instance watch coverage.

**Targets already terminal at boot — RECOMMEND: deliver-once, not mark-and-skip.** The watcher prompt contract states: "The FIRST `[JOB_EVENT]` after your watch is the signal; later events on the same mission's other receipts are echoes — act once. Receipts answer transport questions only (`job_get`); never watch a receipt for a work question. **A revived mission needs a fresh `watch_mission`**; the event carries no epoch" (`agents/_prompt_system/innate-skills/job-orchestration/skill.md:11-14`). Duplicates are therefore **tolerated by design**: an agent that receives a second `[JOB_EVENT]` for an already-acted mission ignores it (act once). Mark-and-skip would need a per-row `state` column plus a second writer on the CAS path, and would risk *losing* the single signal if the first delivery's envelope was dropped in transit. Deliver-once through the existing CAS preserves exactly-once on the delivery side while the agent side absorbs echoes. **State this explicitly in the plan:** duplicate `[JOB_EVENT]` envelopes are an accepted, designed-for degradation, not a defect; the invariant is "at least one delivery reaches the watcher", not "exactly one envelope".

**Double-delivery reasoning:** the CAS is `DELETE … RETURNING` — service tier `work_notifier.py:947-958` calls `watcher_repo.claim_watchers_for_job_for_instances` (`watcher_repository.py:389`, body `:441-452`, `.returning(...)` + commit) **before** any `enqueue_message`, so the DB side is exactly-once; the agent-side "act once" makes the semantic side idempotent. A boot reconcile that re-delivers to an already-claimed row finds zero rows (CAS loser) and stands down — a restart cannot double-fire the DB claim. The only double-fire window is two envelopes for the *same* mission across different receipts, which the prompt contract already absorbs. **No new dedup needed.**

**Revived-instance coverage.** The contract places re-arm on the *watcher agent*: "A revived mission needs a fresh `watch_mission`" (`skill.md:14`). The daemon-side revive paths flip a terminal instance back to RUNNING (`daemon/services/instance_messaging.py:2603-2608` terminal-revival detection; `:2715-2726` the terminal→RUNNING transition; `daemon/manager.py:12378` `_revive_terminal_instance`) but **no job-watcher row is re-armed there** — the only job-watch re-arm writer is the agent-facing tool path (`daemon/tools/job_queue.py` `add_watch` call sites 1618/1650/2781/2869/3114/3117/3145/4472/4541). So the agent-side contract already covers the revive case; a daemon-side auto re-arm would be a convenience *beyond* the contract, not a contract gap.

---

## 4. TTL / cleanup — which sweep to ride

Verified cadences and wiring (all `daemon/api.py` lifespan):

| Service | Cadence | Instantiation | Loop | First-tick | Notes |
|---|---|---|---|---|---|
| `WatchReconcileSweepService` | 300s default (`watch_reconcile_sweep.py:88`; knob `config.py:2915`) | `api.py:2183` (import `:2176-2179`) | `watch_reconcile_sweep.py:398-424` | immediate (`:400` sweep before `:424` sleep) | held-watcher reconcile + zombie-GC + stored-watch GC leg (`:294`); CAS exactly-once; **best fit for watcher-terminal reap + age TTL** |
| `MissionStrandHealer` | 300s default (`mission_strand_healer.py:175`; knob `config.py:2947`) | `api.py:2225` (import `:2211-2214`) | same family shape | immediate | strand-only; terminalizes never-dispatched orphan ghosts; **not** a fit (different invariant) |
| `SourcesGcService` | 3600s default (`config.py:3063`) | `api.py:2299` (import `:2297`) | `sources_gc_service.py:251-252` | immediate | archives+deletes scheduler config rows; predicate at repo layer; **not** a fit (wrong domain) |

**Recommendation: ride `WatchReconcileSweepService`** (extend `reconcile_held_watches_for_instance` with two legs), not a new service:

1. **Watcher-instance-terminal reap:** when the watcher's own `instances.status` is terminal (the `TERMINAL_INSTANCE_STATUSES` set, `daemon/constants.py:1061`), delete its `job_watchers` rows + any `armed` `stored_watches` rows. This mirrors the existing zombie-GC leg (`watcher_repository.py:274` `remove_all_watches_for_job`) but keyed on the watcher, not the job. **ASSUMPTION:** instance-terminal detection reads the instance repository's status set; the exact query method is not re-verified here.
2. **Age-based TTL:** rows older than a configurable window (recommend reusing `MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS` = 6h, `daemon/services/mission_live_guard.py:221`, for consistency with the stored-watch GC) whose target is terminal or unresolvable are reaped. Partially present in the zombie-GC leg; extend the cutoff to cover `job_watchers` rows whose `job_id` no longer resolves.

**Do NOT ride `MissionStrandHealer`** — its invariant is strand-signature-only (never-dispatched ghosts with a terminal direct parent); watcher reap has no strand signature. **Do NOT ride `SourcesGcService`** — different predicate domain and archive lifecycle.

**Orphaned-row hazard post-reap:** reaping a watcher row while a `[JOB_EVENT]` envelope is in flight leaves a dangling notification target. Mitigation: reap must go through the same CAS rather than a raw DELETE, so an in-flight delivery that already claimed the row wins and the reap only removes unclaimed rows. **ASSUMPTION:** a watcher-keyed CAS variant of `claim_watchers_for_job_for_instances` does not exist yet and must be added (the existing method is job-keyed, `watcher_repository.py:389`).

---

## 5. Blast radius

- **Migration:** additive only — one nullable column on `job_watchers` (and optionally `state`); no new table, no backfill required (NULL = unknown mission link; reconcile falls back to resolve-at-notify-time, current behavior). **No frozen contract touched:** C1 buckets (`work_notifier.py:881` `held_for_mission += 1`, `:888` `matching_readonly.append`, `:901` `matching_claimable.append`; declarations `:666/:669/:682`), M2 opt-in default (`watcher_models.py:34-36`, `:60-62`), U1 sweep unchanged. Frozen-contract exposure: none (A/E-class, per options-matrix §2(iv)).
- **Seams changed:** `job_queue_service.reconcile_terminal_watches` (mission-link walk + watcher-terminal reap leg); `WatchReconcileSweepService` (extend `sweep_once`); `watcher_repository` (add watcher-keyed reap + CAS). No change to the `notify_work_watchers` CAS chokepoint (`work_notifier.py:947-958`).
- **Duplicate `[JOB_EVENT]` risk:** low and accepted (§3). A restart can produce one extra envelope per mission across receipts; the prompt contract absorbs it ("act once", `skill.md:11-14`). No regression in exactly-once DB claim.
- **Interaction with mission-resolver liveness vocabulary:** `evaluate_mission_live` (`daemon/services/mission_live_guard.py:327`, returns `MissionLiveVerdict` at `:225`) is the canonical guard; the boot reconcile already consults it for `mission_terminal` rows (`job_queue_service.py:585-601`). The new mission-link column makes that consult cheaper (no resolve walk) but does not change semantics. The brief's `evaluate_mission_terminal` has **no counterpart in the tree** (`git grep evaluate_mission_terminal a1130e257a -- daemon tests` returns nothing) — treat it as the terminal verdict derived from `evaluate_mission_live` (live=False + all-terminal). Flagged for verification.
- **Max-50-watches cap:** `MAX_WATCHES_PER_INSTANCE = 50` (`daemon/tools/job_queue.py:116`), enforced at **FIVE** arm sites. At base the *cap comparison* lines are `job_queue.py:1614` (job_create), `:2762` (watch_job), `:3063` (watch_jobs, `count + len(job_ids)`), `:4470` (watch_mission pre-mission registration), `:4534` (watch_mission receipt arming, `count + len(live_receipts)`); the preceding *count-fetch* lines are `:1613`, `:2761`, `:3062`, `:4467-4469`, `:4533`. A restart that re-hydrates stale rows could push a revived instance over the cap and block new arms. **Mitigation:** the watcher-terminal reap (§4) must run *before* any re-arm on boot, and the cap check (`watcher_repository.py:454` `count_watches_for_instance`) should exclude rows already reaped/skipped. **Orphaned-row hazard post-reap:** if reap deletes a row a live delivery expects, that delivery's CAS finds zero rows and stands down — safe, but the watcher misses that envelope; acceptable under the "at least one delivery" invariant, and the next sweep re-evaluates. **ASSUMPTION:** the cap count reads live `job_watchers` rows with no state filter (`watcher_repository.py:465-469` selects all rows for the instance) — the filter, if adopted, must be added there.
- **RAM buffers (`_pending_injections`):** out of scope for this sketch's table work; RAM-only by design (`manager.py:1773-1781`) and surviving neither restart nor persistence — the injected `HumanMessage` is checkpointed only after drain. The full memory-only inventory (and the sibling DB-backed report-injection path) is in REPORT-1c; if persistence of undrained injections is wanted, that is a separate commission. Flag, do not design here.

---

## Open questions for Emperor

1. Adopt a `mission_instance_id` column on `job_watchers` (recommended) or keep resolve-at-notify-time (current)?
2. Adopt a `state` column for mark-and-skip, or keep existence-as-state + deliver-once (recommended)?
3. Should the daemon auto re-arm `job_watchers` for a revived instance, given the prompt contract already requires the *agent* to re-arm ("A revived mission needs a fresh `watch_mission`", `skill.md:14`)? Convenience beyond contract, or in scope?
4. Does "evaluate_mission_terminal" (brief) map to the terminal verdict derived from `evaluate_mission_live`, or is there a separate function I missed? (Verified: no such symbol in `daemon/` or `tests/` at base.)
5. Should the audit/history gap (deferred-debt (a)) be closed now via the durable `JOB_COMPLETED` witness rows, or deferred until ≥2 silent-loss families confirm?
