> ⚠️ **SUPERSEDED IN PART** — §3's step-2 literal order is superseded by §15.8 / leader ruling L-1 (fail-closed: start row → enqueue → complete row), `_deferred_gen` by `_defer_gen`, the age-cap breach by **Rule A** (ONE guarded abandon → consume + `failed`/`nondelivery_age_cap` only if the guard returned a row; the "fresh generation"/`abandoned_prior` form is superseded — :104/:106/:184), and the arm-time refusal by **DEC-21** (evict+stop the adapter; the "arm with `wake_target_bound:false`" form is superseded — :168/:190); **`decisions.md` (DEC-7, DEC-9, DEC-18, DEC-19, DEC-21, DEC-22) is the implementation contract.**

# Architecture Recommendation: `scheduler-deferred-alarm-delivery`

**Date:** 2026-10-21 (19:0x UTC+7)
**Author:** architect (controller) — aggregation of two dispatched analyst workers + the independent empirical probe lane
**Analyst instances:** `91e08eb3` mechanism/data-flow (⚠️ run flagged `DEGRADED — skill bank miss (data-flow-design)`: worker self-reported no skill injected; analysis accepted on evidence — git-verified tree, verbatim predicate quotes), `3758e60a` failure-modes (`Skill loaded: [resilience-design]`)
**Empirical basis:** probe lane P1–P5 (real PG, fenced `ensemble_dev`, throwaway schema) — treated as ground truth where it overlaps static analysis
**Tree:** worktree `/home/nea/ensemble-src-wt-sched-deferred-alarm` @ `8e6df58e4` (all anchors read/verified there; `decisions.md` / `plan-overview.md` by planner are siblings of this file)
**Status:** COMPLETE — one planner-DEC conflict flagged for leader adjudication (§13, ADR-1 vs DEC-8/DEC-14.2). **No conflict with any leader ruling R1–R10.**

---

## 1. Problem & constraints recap

**Defect (settled, not re-litigated):** three drop sites in `daemon/sources/adapters/scheduler.py`, one mechanism — every one fires a completion callback on an `execution_id` that was never INSERTed (registry `_safe_sync_callback` routes `skipped` → `record_execution_complete` = pure UPDATE on a missing row, `daemon/sources/registry.py:473-494` → `daemon/repositories/source/repository.py:618-643`, no upsert), then returns before `_execute_run`'s `triggered` INSERT (`scheduler.py:964`):

1. **Scheduled reuse-mode busy branch** `:1194-1213` — blocking set `{RUNNING, WAITING, WAITING_CHILDREN}` (the predicate is `:901-905`), bare `return` at `:1215`.
2. **Manual-trigger twin** `:1241-1258` (reuse-gated; R9: deferred, disclosed non-goal).
3. **Semaphore-timeout skip** `:909-942` — called at `:1189-1190` **before** the reuse check at `:1195` → reachable for **every** `instance_mode` (probe P5).

Then `_run_schedule:620-632` consumes the one-shot **unconditionally** (`_is_one_time_executed=True` `:624`; `registry.py:514-525` stops + disables) — and `_execute_run` swallows exceptions (`:1030-1040`), so even a normal return proves nothing about delivery. `last_run_at` is *derived* from the latest execution row (`daemon/routers/schedules.py:118-126`) → **a lost alarm is observationally identical to a delivered one.** Busy-branch log is INFO.

**Hard constraints:** leader rulings R1–R10 (durable pending handoff; start→complete row on every skip/defer + WARNING; count-in-message; R4 visibility; **no new table / no migration**; paused caller keeps delivery; keep the blocking set; E1 refusal at the mapper; manual twin out; semaphore sibling in). Enabling primitive is confirmed and mandated: `InstanceManager.enqueue_message_job` (`daemon/manager.py:9501`) — never the `send_message` RAM-injection lane (`daemon/tools/instance.py:4138`).

---

## 2. P1–P5 reconciliation (explicit, as required)

| Probe | Verdict | Design consequence here |
|---|---|---|
| **P1** enqueue-to-busy QUEUES | Task PENDING + MQ + JobItem queued; `_pending_injections` empty; real `AsyncMessageResult` | The deferral substrate is proven — no new delivery primitive (R1 satisfied by construction) |
| **P2** claim gate serializes per-instance | `instance_id NOT IN (SELECT instance_id FROM task WHERE status=:running)` (`task/repository.py:2920`); proven at claim boundary only | Q8's ordering guarantee rests on this + `created_at ASC` ordering; the "not through a real graph.astream" gap is disclosed as a test-pack obligation, not a design risk |
| **P3** key semantics narrower than feared | Predicate `idempotency_key IS NOT NULL AND deleted_at IS NULL`, **no status term**; `deleted_at` only via explicit delete (`job_queue/repository.py:4373`); terminal predecessor **bypassed** by `JobQueueService.enqueue` (`job_queue_service.py:1447-1481`, mints `key#uuid8` → fresh JobItem, new payload, delivers); non-terminal predecessor → true dedup hit → loser Task CANCELLED, loser MQ DELETED, **kept payload FROZEN** (`instance_messaging.py:3089-3190`) | Q2 answered precisely: key freed **only** by `deleted_at` or the terminal-bypass. "Constant key permanently poisons" REFUTED. The real hazard = the non-terminal race window → drives the Q1 verdict (never re-enqueue under a colliding key while a delivery is live) |
| **P4** baseline defect reproduced | busy branch → 0 rows of every kind, one-shot consumed; never calls `_record_skipped_execution` | Confirms R2's fix shape: the ADR-003 lateness template (`scheduler.py:759-821`) is 200 lines above the defect and does exactly what the pin requires |
| **P5** semaphore site precedes reuse check | identical zero-row shape + one-shot consumed, **any** `instance_mode` | Q10 two-branch fix: reuse+mapping → defer to pinned caller; no-target → **re-arm** (do not consume) + visible row + mandatory 5s sleep (hot-loop proof, §11) |

---

## 3. Q1 — MECHANISM (verdict + 5-axis matrix)

### RECOMMENDATION — **Shape (ii): durable pending delivery via `enqueue_message_job`, coalesced by a guarded IN-PLACE update of the still-pending delivery's `message_queue` row. No teardown, no re-enqueue while a delivery is live.**

The design, concretely (busy branch, reuse-mode, both skip sites):

1. **Pin the target.** Use the `instance_id` the busy check already resolved — never re-enter `mapper.get_or_create_instance` at defer or delivery time (there are **TWO** stranger-spawn surfaces in the mapper — the stale-mapping branch `mapper.py:336-344` **and** the no-mapping fall-through `mapper.py:352-360`; fire-time E1 covers the **stale-mapping branch only**, the no-mapping branch is closed by the arm-time refusal, which is why the adapter must actually be evicted+stopped; `enqueue_message_job` targets an instance directly and revives rather than spawns — the terminal-revival set is `instance_messaging.py:2369-2374` and the assignment `:2378-2379`).
2. **First deferred fire of a generation:** write the generation marker (`_deferred_gen`, `_defer_message_id`, `_defer_count`, `_defer_since` — atomic JSONB siblings of `_run_counter`, `source/repository.py:147-196`), then enqueue via `manager.enqueue_message_job(instance_id=<pinned>, content=<composed with count=1>, priority=5, idempotency_key=<key per Q2>)`. Capture the returned `message_id` into `_defer_message_id`. Write the DEFERRED execution row (R2, §12/DEC-16 shape). Log WARNING.
3. **Later fires while the delivery is live:** do **not** enqueue. Recompose the body with the incremented count, re-run the existing scrub (`scrub_enqueued_body`, the same helper `instance_messaging.py:2179/2181` uses), and issue a **guarded single-statement update** on the pending delivery: `UPDATE message_queue SET content=:c, message_metadata=:m WHERE message_id=:id AND status IN ('ready','retrying')`. **[SUPERSEDED: the narrow `status='ready'` form is superseded by the leader-ruled W9 widening — `decisions.md` DEC-18/DEC-12]** Rowcount 1 → count committed to config. Rowcount 0 → the row was claimed/consumed/gone → **generation rollover**: bump `_deferred_gen`, enqueue fresh under the new generation key (§4), reset count to 1.
4. **Marker-miss fallback (crash self-heal):** if `_defer_message_id` is absent but a live deferred delivery may exist, enqueue under the *generation* key — a non-terminal predecessor produces a **dedup hit that returns the KEPT `message_id`** (Worker A §8; P3), after which step 3's guarded update corrects the kept body's count. The dedup path that freezes payloads becomes the recovery mechanism; no loss, no duplicate.
5. **One-shot consumption:** only after the enqueue returns real ids (§5). **[SUPERSEDED: durable evidence is PER-DELIVERY — a PENDING Task whose `message_id` EQUALS this enqueue's id; a real enqueue id is necessary but NOT sufficient (Blocking 1; `decisions.md` DEC-9)]**

**Why this satisfies R3 (count in the delivered message):** the body carries the count and is mutated in place on every coalesced fire; the only window where the count can be stale is update-vs-claim (ms-scale), where the delivered body lags by exactly one occurrence and the authoritative `_defer_count` (R4) is exact.

### Alternatives considered — and rejection rationale

- **(i) Teardown-then-re-enqueue (planner DEC-8):** soft-delete the pending JobItem (`JobRepository.soft_delete`, frees the key), cancel the Task, delete the MQ, re-enqueue fresh with the live count. **Rejected — CONFLICT flagged (§13, ADR-1).** Three grounds, all evidence-cited:
  - **The race is check-then-act, not atomic.** DEC-14.2 concedes a "millisecond-scale double delivery" between the "still PENDING" re-read and `cancel_task`; the proposed mitigation (re-read immediately before cancelling) *narrows* but cannot *close* the window — the claim UPDATE (`task/repository.py:2920`) can land between the read and the cancel. A double delivery violates the wake-the-caller doctrine's "**exactly one message**, never a spawn" (user ruling 2026-10-20, live v0.26.2). At-most-once is the invariant this commission exists to restore; trading it for count fidelity inverts the priority.
  - **Teardown is the bigger "second writer".** DEC-8 rejects in-place update as "a second writer on a row the queue owns" — but teardown performs **three** mutations of queue-owned rows (cancel Task + delete MQ + soft-delete JobItem) per coalesced fire, versus one **conditional, single-statement** update that can only land while the row is still in its pre-claim `ready` state. The objection applies a fortiori to DEC-8.
  - **Scrub-seam objection is neutralizable.** DEC-8's first ground ("bypasses the scrub seam") is answered by **calling the same scrub helper** on the recomposed body before the guarded update — the seam is a function, not a location.
  - DEC-8's own documented fallback (option C, frozen count + live count on the visibility surface) is *exactly* shape (ii)'s worst case — except (ii) reaches that worst case only inside a ms-scale claim race instead of by design, and never risks the duplicate.
- **(iii) Armed re-check (lateness-cap style):** keep the schedule armed with a sentinel; reuse the existing 5s retry loop; compose fresh at delivery. **Rejected.** (a) Conflicts with R1 — no durable pending artifact is handed off; the intent stays in an in-memory adapter loop. (b) **Hot-loop proof:** a re-armed past-due one-shot returns `now` from `_get_next_trigger_time` (`:753`) → `wait_seconds ≈ 0` → the loop re-fires with no sleep; the only sleeps are the `PAST_LATENESS_CAP` branch (`:580-582`) and the exception handler — with `one_shot_max_lateness_seconds` unset live (default `None`, `config.py:2667-2673`) the cap branch is dead → tight spin. (c) Crash/restart past the cap converts to a SKIPPED row (lost intent) — relocating the defect.
- **Leader-literal (A) "enqueue + lookup-then-UPDATE on later fires":** this *is* shape (ii) — the probe's consequence #2 narrows it: the lookup must be by the persisted `_defer_message_id` marker (with the dedup-return fallback), never by re-enqueueing under a colliding key (P3 freeze).

### 5-axis trade-off matrix (Q1)

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | R3 |
|---|---|---|---|---|---|---|
| **(i) teardown-then-re-enqueue** (DEC-8) | **Med** — read-pending + 3 teardown calls + re-enqueue + generation bookkeeping | **Med** — N×(cancel+delete+soft-delete+insert) churn per episode | **Low-Med** — implicit contract with job-queue internals (soft-delete semantics) | **HIGH** — check-then-act race → duplicate delivery (doctrine violation); JobItems hidden from listings via `deleted_at` | Med | Fully live count, at duplicate risk |
| **(ii) enqueue-once + guarded in-place update** ← RECOMMENDED | **Low-Med** — one additive repo method (guarded, reuses scrub helper) + config marker | **High** — O(1) per coalesced fire; ≤1 pending delivery per schedule/generation | **High** — single writer, single conditional statement, no job-queue mutation | **Low** — update-vs-claim race degrades to a one-behind count (guarded no-op); at-most-once preserved by construction | **Low** | Live except ms-scale claim race; authoritative count on R4 |
| **(iii) armed re-check** | **Low** concept / **High** operational (sentinel, sleeps, lateness interplay) | **Low** — spin per tick while caller busy | Med | **HIGH** — hot loop (proven, cap unset), loss past cap, R1 conflict | Low-Med | Live at delivery — but delivery not guaranteed |

**Which satisfies R3:** (ii) and (iii) put the count in the delivered message; (i) also does — but (i) is the only one that can deliver the message **twice**, and (iii) is the only one that can fail to deliver at all. R3 fidelity ranking: (i) ≈ (iii) > (ii) by one ms-scale race; safety ranking: (ii) ≫ (i) ≫ (iii). **Recommendation: (ii).**

---

## 4. Q2 — IDEMPOTENCY-KEY LIFECYCLE (the pivot, restated precisely)

**Facts (probe P3 + DDL):** `idx_job_idempotency` predicate = `idempotency_key IS NOT NULL AND deleted_at IS NULL` (`job_queue/models.py:301-306`, confirmed in live PG catalog) — **no status term**. The key is freed **only** by (a) `deleted_at` set via explicit delete/soft-delete (`job_queue/repository.py:4373`), or (b) the **service-level terminal-bypass**: a terminal predecessor (`admission_state ∈ done/dead`) causes `JobQueueService.enqueue` to mint `f"{key}#{uuid4().hex[:8]}"` and create a fresh JobItem with the caller's payload (`job_queue_service.py:1447-1481`) — confirmed on the scheduler's exact enqueue chain (`manager.py:9501` → `instance_messaging.py:2695/:3059/:3077` → `job_queue_service.enqueue`). A **non-terminal** predecessor → true dedup hit → loser Task cancelled, loser MQ deleted, kept payload frozen (`instance_messaging.py:3089-3190`).

**RECOMMENDATION — safe key shapes (keep planner DEC-7, with role narrowed):**
- **One-shot:** the existing **run_at-scoped** key `scheduler:{source_id}:{configured_run_at.isoformat()}` (`scheduler.py:1112-1116`) — already derived from the *configured* `run_at`, never `next_trigger`/`now` (the D4 trap). Per-occurrence at-most-once: crash-re-fire of the same occurrence dedups; a *different* occurrence gets a different key.
- **Recurring deferral:** **generation-scoped** `scheduler:{source_id}:deferred:v{N}` with `_deferred_gen` in `source_configs.config` (DEC-7). A new generation opens exactly when the current one is no longer a live pending delivery (guarded-update rowcount 0). The generation marker — not the key — is the authoritative coalescing state; the key is only the dedup latch.
- **Key must change:** at generation rollover (recurring) — never per-fire; never reuse the run_at key for recurring coalescing (per-occurrence keys would produce N deliveries — R3's coalescing intent defeated); never a bare constant key as the *primary* mechanism (collapses "pending" vs "terminal" into one branch and makes episode lookup depend on the bypass's synthetic key — DEC-13's lookup-integrity reason).

**Failure modes owned:** a same-key enqueue against a terminal predecessor terminal-bypasses → duplicate delivery in one narrow window (one-shot: enqueue-commit → delivery-completes → crash → consumption already done is required for this to matter — see Risk R2). A same-key enqueue against a non-terminal predecessor freezes the kept payload — in shape (ii) this only occurs in the marker-miss fallback, where freezing is corrected by the subsequent guarded update on the returned (kept) `message_id`.

---

## 5. Q3 — ONE-SHOT CONSUMPTION

**RECOMMENDATION — consume at defer time, CONDITIONAL on durable evidence; at BOTH skip sites.** The consumption write (`_is_one_time_executed=True` + `on_complete_callback`, `scheduler.py:624-629`) moves behind the deferral's success predicate: the enqueue must have produced a PENDING Task whose `message_id` EQUALS the id it returned (P1's `AsyncMessageResult`; the Task row carries `message_id`, `instance_messaging.py:2316-2320`), and the DEFERRED execution row must have been written. **[SUPERSEDED: durable evidence is PER-DELIVERY — a PENDING Task whose `message_id` EQUALS this enqueue's id; a real enqueue id is necessary but NOT sufficient (Blocking 1; `decisions.md` DEC-9)]** (R2; fail-closed on row-write failure — a failed audit row is preferable to a silent dispatch, the ADR-003 doctrine). This keeps DEC-9's shape (the existing consumption point is unchanged in *location*, only gated) — strictly additive.

**Failure modes, stated:**
- *Keep-armed-until-delivery (rejected):* requires observing delivery completion — the adapter only observes enqueues; new plumbing (a delivery-executed callback), which the commission forbids in spirit (R1 additive-only) and which re-imports G3's duplicate-fire class (DEC-9's own rejection).
- *Consume-at-defer (chosen):* crash after enqueue-commit, before consumption → adapter restart rebuilds from `run_at`; re-fire dedups on the run_at key (same occurrence) → single delivery → then consumption proceeds. Crash after consumption → delivery already durable in Task/MQ/JobItem (survives restart; the boot sweeps target `running` orphans, not `pending`). Residual: the ms window where the delivery *completes* before the consumption write and a crash lands between → re-fire terminal-bypasses → duplicate (visible, bounded — Risk R2).
- *Semaphore site (new_instance / no target):* **RE-ARM, do NOT consume** (§11) — there is nothing durable to carry the intent, so consuming would be the original defect.

---

## 6. Q4 — DEFERRAL BOUND

**RECOMMENDATION — two bounds, both checked at FIRE TICKS ONLY (no polling loop):**
- **Count cap = 16** per generation (mirrors `PENDING_WAKE_COALESCE_MAX`, `daemon/tools/upgrade_journal.py:1101`). On saturation: **keep coalescing into the SAME delivery** (the guarded update continues), **pin the count at the cap**, disclose "16+ occurrences coalesced" in the body (a saturating display — **not** `min(count+1, cap)`, which would write "16"); **never drop the delivery**. **[SUPERSEDED: the pre-Blocking-4 "stop incrementing" form is superseded — the `count < cap` gate is REMOVED from the coalescing branch, so a saturated generation can never fall through to a second enqueue while a delivery is pending (`decisions.md` DEC-19)]**
- **Age cap = 24h default**, new `SchedulingConfig` fields (pydantic-settings, env `ENSEMBLE_SCHEDULING_DEFER_MAX_AGE_SECONDS` / `..._DEFER_MAX_COUNT`; field style precedent `one_shot_max_lateness_seconds` at `config.py:2667-2673`). On breach at the next fire tick: close the generation **visibly** via the 2-call row template (`_record_skipped_execution` shape, `scheduler.py:759-821`) with `error_message="deferred episode abandoned: N occurrences over Thh"`, then open a fresh generation whose composed body carries `abandoned_prior` — **[SUPERSEDED-BY-RULE-A — the leader-ruled age-cap outcome is the ONE rule: ONE guarded abandon (`WHERE status IN ('ready','retrying')` → terminal abandoned) → consume + a terminal `failed` row (reason `nondelivery_age_cap`) + WARNING **only if** the guard returned a row, else NO consumption and NO failed row (the in-flight delivery will land); the carrier clears; the breach **NEVER opens a fresh generation**. The "close the generation + open a fresh one carrying `abandoned_prior`" form stated here is the **REJECTED Rule B** — it re-opens the one-shot spin and can double-deliver. `decisions.md` DEC-9 / DEC-19 / DEC-22 are the implementation contract.]**`abandoned_prior=N`.
- **Do NOT reuse `one_shot_max_lateness_seconds`:** it measures *fire lateness* (`now - run_at`), is consumed only in the ONE_TIME past-due branch (`:725-752`), and is unset live; the live recurring alarm (`self-alarm:nest-patrol-recurring`, cron `0 */2 * * *`) is structurally outside it.
- **Breach is never silent:** the abandonment row + R4 fields (`deferred_count`, `last_execution_status`) + WARNING log; the fresh generation's first delivery restates the abandoned count in-message (R3). **[SUPERSEDED-BY-RULE-A — the leader-ruled age-cap outcome is the ONE rule: ONE guarded abandon (`WHERE status IN ('ready','retrying')` → terminal abandoned) → consume + a terminal `failed` row (reason `nondelivery_age_cap`) + WARNING **only if** the guard returned a row, else NO consumption and NO failed row (the in-flight delivery will land); the carrier clears; the breach **NEVER opens a fresh generation**. The "close the generation + open a fresh one carrying `abandoned_prior`" form stated here is the **REJECTED Rule B** — it re-opens the one-shot spin and can double-deliver. `decisions.md` DEC-9 / DEC-19 / DEC-22 are the implementation contract.]**

---

## 7. Q5 — DEAD-CALLER RELOCATION HAZARD

**The feared hazard is REFUTED for enqueue-now designs:** `_prepare_enqueued_message` **revives terminal callers in the same transaction as the Task insert** (`instance_messaging.py:2369-2374` — the terminal-revival set `COMPLETED|TERMINATED|ERROR|FAILED` — and the assignment to RUNNING at `:2378-2379`; commit `:2482`) — so the claim gate's TERMINATED exclusion (`task/repository.py:3029`) is **unreachable** for a deferred delivery, and waking a terminal caller **is** the intended wake semantics (the ruling pins the target *instance*, not a live turn; a COMPLETED caller is the normal end-state of every finished turn). No re-targeting, no fail-loud needed at defer time.

**Residual failure modes (the real hazard set) — all mitigated by visibility, none by new machinery:**
1. **Terminate-after-enqueue:** the terminate cascade `DELETE`s MQ (`instance_lifecycle.py:5424-5427`) and Task (`:5446-5449`) → the deferred delivery is destroyed. The DEFERRED execution row + `_defer_count` (written at defer time) keep the loss **visible**; WARNING log. Recurring alarms self-heal at the next fire (new generation).
2. **Instance row GONE (out-of-band delete):** no revive → ghost Task for a nonexistent instance. First line of defense is E1 (§12): refuse-to-arm at tool time, refuse-to-spawn at mapper time; a deferred fire whose pinned instance is confirmed gone → fail-closed, visible FAILED/DEFERRED row, consume-visibly (one-shot).
3. **Post-revive re-terminal:** a stranded `process_message` Task is NOT swept (the drift sweep covers `process_report` only, `job_recovery_service.py:2065`). Any later revive of the instance (any send) flips it claimable → delivered. Until then: visible via the DEFERRED row. **Named as a follow-up lane, not fixed here.**

---

## 8. Q6 — PAUSED-CALLER MECHANICS (mechanism named, not assumed)

Verbatim gate (`task/repository.py:3008-3030`): the pause clause excludes `instances.status IN (:status_paused, :status_terminated)` for **ALL** task types — sole carve-out `heartbeat_emit_stuck`. `COMPLETED/ERROR/FAILED` are *not* in this gate (they are handled by enqueue-time revival, §7).

- **While paused:** the PENDING deferred Task **persists unchanged** — the pause cascade touches only `status='running'` rows (`instance_lifecycle.py:3762-3768`) — and the gate refuses it.
- **On resume:** the instance flips to RUNNING and `resume_instance_cascade` fires an **explicit `safe_notify_all_pools`** (`instance_lifecycle.py:4456-4459`, comment `:4386-4396`: queued-during-pause tasks are "immediately reconsidered"). Fallbacks: the worker-pool 3s poll (`worker_pool.py:371`) and `EligiblePendingSweepService` (90s, `eligible_pending_sweep.py:29-30`) which heals a lost notify.
- **"Delivery survives pause" means exactly:** trio persists → gated while PAUSED → delivered on the **first turn after resume** via the explicit notify pulse. This matches R6 and DEC-11; U1 is thereby answered.

---

## 9. Q7 — VISIBILITY MODEL

Aligned with planner DEC-12 (no conflict):
- **`deferred_count`** = occurrences coalesced into the current-or-last deferral generation **since the last DIRECT dispatch**. **Reset points (no delivery-executed callback needed):** (a) inside `_execute_run`'s reuse-mode dispatch path — the only place the adapter observes a direct delivery; (b) at generation rollover (set to 1). Saturates at 16 with in-body "16+" disclosure.
- **`last_execution_status`** = `get_latest_execution(schedule_id).status`, now including the new `ExecutionStatus.DEFERRED` member. **`schedule_executions.status` is TEXT with only a NOT NULL constraint** (verified live PG) → enum member is pure Python, **no migration**.
- **Storage:** `source_configs.config` JSONB keys (`_defer_count`, `_defer_gen`, `_defer_message_id`, `_defer_since`) via the atomic `jsonb_set`/`json_set` pattern (`source/repository.py:147-196`), the same substrate `_run_counter` already uses. Single-writer discipline: the DEFERRED row is written **adapter-direct** (2-call, `deferred-<uuid>` id, registry QUEUED callback suppressed on the deferred enqueue — DEC-16, avoiding the executor-thread unordered-write race).
- **`_run_counter` stays separate and is NOT incremented at defer:** it counts `_execute_run` dispatches (`:975`); deferred deliveries are queue-routed and never pass `_execute_run`. Keeping them separate preserves "counter = adapter dispatches" semantics and avoids racing the counter from the busy branch; the divergence (a deferred delivery does not advance `#N [CONTINUATION]` numbering) is documented and pinned.

---

## 10. Q8 — ORDERING & CONTEXT INTEGRITY

- **Strictly after the current turn: guaranteed** by the per-instance claim gate (`task/repository.py:2920`) — one RUNNING task per instance; the PENDING deferred Task is claimable only after the running task completes (P2). **Documented exception:** the `PROCESS_REPORT` wake-lane is ordered *ahead* of everything (claim ordering: wake-lane first, then `created_at ASC`, no priority term) — a child report can legitimately precede the deferred alarm's turn.
- **Blocking-set divergence (pin it, per R7):** the adapter defers on `{RUNNING, WAITING, WAITING_CHILDREN}`; the queue serializes only against RUNNING. So a deferred delivery to a **WAITING** caller is claimable immediately, and **WAITING_CHILDREN** is auto-resumed to RUNNING then claimable (`instance_messaging.py:2375-2379` — the `IDLE`/`WAITING_CHILDREN` → `RUNNING` branch; the terminal-revival set at `:2369-2374` is a **different** branch and must not be cited for this) — i.e., under the fix these states now **receive** the alarm (as the next serialized turn) instead of being dropped. Turn-safety is preserved by the gate; only timing changes. Pin with a test.
- **Context fidelity:** `content` + `message_metadata` (JSONB) persist into the delivered HUMAN message (`instance_messaging.py:2182-2212`, `:2311-2342`); body secret-scrubbed pre-persist (`:2181`) — and **re-scrubbed before any in-place update** (§3). `priority=5` + HUMAN ⇒ the fresh-episode attestation reset (`priority==1 and type==HUMAN`, `:2420-2422`) does **not** fire — keep priority 5 (DEC-10); document the coupling. `task_context` is skipped on retry/resume (`:4559`) — irrelevant to the scheduler path (no `task_context` passed).

---

## 11. Q9 + Q10 — COALESCING SCOPE & BLAST RADIUS

**Q9 — per-schedule coalescing is SUFFICIENT; per-caller coalescing REJECTED.** Claim ordering is FIFO by `created_at` per instance, so N deferred deliveries for one caller land in fire order (ties unspecified). Live census (prod PG, read-only): **14 scheduler sources; exactly 3 `reuse_instance` — all three `self-alarm:*`, all carrying `bound_to_caller`, all pinned to the SAME instance** (`dac6a958…`); worst case today = 3 FIFO wake messages, each labeled with its own schedule + count. Per-caller coalescing would couple independent schedules' generations for marginal benefit and has no substrate support; the R3 count-in-message plus per-schedule labels already make the intent legible.

**Q10 — blast radius, precisely:**
- The **busy branch is reuse-only** (`:1195` gates the whole check; manual twin likewise `:1239-1257`) → the deferral change affects **all reuse-mode schedules** (today: the 3 self-alarm rows above). **Apply ungated** — `source_type` is unusable for gating (every self-alarm row is `source_type="scheduler"`), reuse-mode semantics (deliver-as-next-turn) are correct for any consumer, and a gating flag would fork behavior for zero live benefit. The E1 refusal, keyed on `bound_to_caller`, is the only self-alarm-specific behavior — non-self-alarm reuse mappings (none live) keep the legacy re-spawn path.
- The **semaphore site hits every `instance_mode`** (P5: `:1189-1190` precedes `:1195`) → two-branch fix: (branch 1) reuse-instance + resolvable mapping → defer to the pinned caller (same mechanism); (branch 2) new_instance / no target → **re-arm**: do NOT consume the one-shot, write the execution row via start→complete with `DEFERRED` + reason, and **sleep `SCHEDULER_ERROR_RETRY_S` (5.0s, `constants.py:376`)** exactly like the `PAST_LATENESS_CAP` branch — a re-armed past-due one-shot otherwise re-fires at `wait≈0` (hot-loop proof, §3). The age cap (§6) bounds total re-arm spin.
- R9 honored: the manual twin is untouched, disclosed as a non-goal (its skip is a *pure* loss today — no row, no consumption — noted for the record; DEC-3 already records the follow-up path).

---

## 12. Q11 — E1 ARCHITECTURE (binding integrity)

**Placement is SAFE (verified against every caller of `mapper.get_or_create_instance`):** `scheduler.py:1086` (self_alarm + reuse), `registry.py:1029` (discord/slack/telegram — proven live: 12 discord mappings, **none** carry `bound_to_caller`), `mapper.py:452` (`handle_incoming_message`). A refusal keyed on `mapping["metadata"].get("bound_to_caller")` (metadata already in hand from `get_mapping`, `mapper.py:235` — zero extra I/O) cannot affect any non-self-alarm mapping.

**Refusal contract (exact):**
- **Fail-closed on CONFIRMED absence** (`_mapping_instance_exists` returned `False` from a successful query): raise a typed error **before** the delete+spawn (`mapper.py:336-344`); do NOT delete the mapping; do NOT spawn; WARNING log with `source_id` + `dead_agent_instance_id`. The binding survives for a later re-bind.
- **Indeterminate (DB error) stays fail-open-to-RETURN-EXISTING, never spawn:** the current `except → return True` (`:198-210`) routes to the return-existing branch (`:346-351`) and never reaches delete+spawn — preserve this property verbatim; the refusal is about stranger-spawn, not about liveness verification.
- **Preserve binding keys at the mapper's metadata-construction site (`:370-373`)** — `create_instance_mapping`'s upsert replaces `mapping_metadata` wholesale (`source/repository.py:387`); without this, any later mapper-driven re-create silently unbinds the alarm.
- **Tool surface (closing the unbound-but-armed hole):** `_bind_schedule_to_caller` (`self_alarm.py:873-957`, best-effort `return None` at `:942-950`) must **REFUSE TO ARM** when the binding cannot be written — an armed alarm with no binding later fires into the E1 spawn path. Arm-with-warning is rejected: the failure would surface only at fire time with no owner watching. Response carries `wake_target_bound:false` + a reason code (`binding_target_dead` / `binding_write_failed`). **[SUPERSEDED — this paragraph carried the PRE-RULING arm-time contract (`response carries `wake_target_bound:false`), which leaves the **ADAPTER RUNNING** (`enabled` is read only at boot, `registry.py:291`; the scheduler loop never re-reads it) → the next tick takes the **no-mapping fall-through** (`mapper.py:352-360`) → a stranger `ari` spawn — the exact breach DEC-21 exists to close. The LEADER-RULED D-A form (DEC-21) is: a **typed error** naming the binding failure, the row **RETAINED** as `status='stopped'` + `enabled=False` (not deleted), **AND evict + stop the adapter** via the existing seam (`scheduling_service.py:527-549` — `registry.unregister` + `await adapter.stop()`); the named assertion is that the schedule is not armed **AND** the adapter is not registered/running **AND** no mapping exists **AND** a forced fire yields no delivery **AND** no spawn occurs. `decisions.md` DEC-21 is the implementation contract.]**
- **One-shot on E1 refusal: CONSUME VISIBLY** (terminal condition — the bound instance row is gone and will not return; re-arm would spin against a dead target). The refusal reason goes into the execution row's `error_message`; R4 surfaces it. Contrast: busy-deferral is transient → defer; E1 is permanent → consume + record.
- Never trust the tool response's async `status` field ("starting" vs actual "running") — surface reads come from `source_configs` readback.

---

## 13. DECISION LIST (ADR-style)

> Conflicts: **ADR-1 conflicts with planner DEC-8 (+DEC-14.2, mooted) — flagged for leader adjudication.** No decision conflicts with any leader ruling R1–R10 (verified per-item). All other planner DECs are affirmed.

| # | Decision (context → decision → consequences) | Status / conflict |
|---|---|---|
| **ADR-1** | *Ctx:* coalescing must keep a live count in the delivered message without breaking at-most-once. *Dec:* shape (ii) — guarded in-place `message_queue` update (re-scrubbed body, `status IN ('ready','retrying')` guard, **[SUPERSEDED: the narrow `status='ready'` form is superseded by the leader-ruled W9 widening — `decisions.md` DEC-18/DEC-12]**, generation marker authoritative); teardown-then-re-enqueue rejected on the check-then-act race (duplicate delivery = doctrine violation), the second-writer asymmetry, and the neutralizable scrub objection. *Cons:* one additive repo method; count can lag one occurrence in a ms-scale claim race (authoritative counter covers it). | **CONFLICTS with planner DEC-8; supersedes DEC-14.2 (race eliminated, not bounded). Leader adjudicates.** |
| **ADR-2** | *Ctx:* P3 key semantics. *Dec:* one-shot → existing run_at key; recurring → DEC-7 generation key (`scheduler:{source_id}:deferred:v{N}`); key changes only at generation rollover; key = dedup latch, config marker = coalescing state; terminal-bypass = safety net, not primary mechanism. *Cons:* no reliance on `key#uuid8` semantics in the happy path. | Affirms DEC-7/DEC-13 (role narrowed). No ruling conflict. |
| **ADR-3** | *Ctx:* P4 red pin. *Dec:* one-shot consumed at defer, conditional on durable evidence (a PENDING Task whose `message_id` equals this enqueue's id + DEFERRED row written). **[SUPERSEDED: durable evidence is PER-DELIVERY — a PENDING Task whose `message_id` EQUALS this enqueue's id; a real enqueue id is necessary but NOT sufficient (Blocking 1; `decisions.md` DEC-9)]**, both skip sites; semaphore-no-target branch re-arms (never consumes). *Cons:* strictly additive; ms-scale duplicate window disclosed (R2). | Affirms DEC-9. No ruling conflict. |
| **ADR-4** | *Ctx:* UPDATE-on-never-INSERTed-uuid. *Dec:* explicit start→complete DEFERRED row on every skip/defer/refusal/abandonment (2-call template, `deferred-<uuid>` id, adapter-direct single writer, registry QUEUED callback suppressed on the deferred enqueue); busy log → WARNING. *Cons:* deterministic rows, writable pins. | Affirms R2, DEC-16. No ruling conflict. |
| **ADR-5** | *Ctx:* unbounded deferral. *Dec:* count cap 16 + age cap 24h default (new `SchedulingConfig` fields, env-tunable); breach → visible abandonment row + fresh generation carrying `abandoned_prior`; checks at fire ticks only. *Cons:* two config knobs; no polling loop. | New (planner had saturation only, DEC-9a). **[SUPERSEDED-BY-RULE-A — the breach half of this row ("breach → visible abandonment row + fresh generation carrying `abandoned_prior`") is the **REJECTED Rule B**; the leader-ruled Rule A is: ONE guarded abandon → consume + terminal `failed` row (reason `nondelivery_age_cap`) + WARNING **only if** the guard returned a row, else no consumption and no failed row; the carrier clears; the breach NEVER opens a fresh generation. This status cell's "No ruling conflict" is **WITHDRAWN as false**. `decisions.md` DEC-9 / DEC-19 / DEC-22 are the implementation contract.]** |
| **ADR-6** | *Ctx:* dead caller. *Dec:* enqueue-time revive is the intended wake; no re-target; residuals (terminate-race, row-gone, re-terminal) mitigated by visibility + E1; `process_message` drift-sweep gap named as follow-up. *Cons:* no new machinery. | New adjudication. No ruling conflict. |
| **ADR-7** | *Ctx:* R6. *Dec:* PAUSED caller: PENDING persists through pause, gated while paused, delivered on first turn after resume (explicit `safe_notify_all_pools`). | Affirms R6, DEC-11; answers U1. No ruling conflict. |
| **ADR-8** | *Ctx:* R4/R5. *Dec:* `_defer_count` + `last_execution_status` in `source_configs.config` JSONB + `ScheduleDetail`/`_row_to_detail` + self_alarm responses; reset points fire-time-computable; `ExecutionStatus.DEFERRED` = Python-only; `_run_counter` untouched and documented divergent. | Affirms R4, R5, DEC-12. No ruling conflict. |
| **ADR-9** | *Ctx:* R7. *Dec:* blocking set unchanged; queue-side timing divergence (WAITING claimable / WAITING_CHILDREN resumed) pinned as a test. | Affirms R7. No ruling conflict. |
| **ADR-10** | *Ctx:* P5. *Dec:* busy branch fix applies to ALL reuse-mode schedules (ungated); semaphore site two-branch (defer / re-arm+5s-sleep+row); manual twin untouched (R9). | Affirms R9/R10, extends DEC-2 with the no-target branch. No ruling conflict. |
| **ADR-11** | *Ctx:* E1. *Dec:* mapper refusal fail-closed on confirmed absence, before delete+spawn, keyed on `bound_to_caller`; DB-error path preserved fail-open-to-return-existing; binding keys preserved at `:370-373`; tool refuses-to-arm on binding failure; E1 one-shot consumes visibly. | Affirms R8. **[SUPERSEDED — the tool-surface half of this row carried the PRE-RULING arm-time contract (`wake_target_bound:false`, no evict+stop) and this status cell's "No ruling conflict" is **WITHDRAWN as false**; the LEADER-RULED D-A form is DEC-21: typed error + row RETAINED `status='stopped'`/`enabled=False` + **evict+stop** via `scheduling_service.py:527-549` + the stopped/unregistered/no-mapping/no-delivery/no-spawn assertion.]** |
| **ADR-12** | *Ctx:* per-caller vs per-schedule. *Dec:* per-schedule coalescing sufficient; FIFO `created_at` cross-schedule; per-caller rejected. | New adjudication. No ruling conflict. |

---

## 14. RISK REGISTER (top 5)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R1** | Count-fidelity race: claim lands between the guarded update's lookup and its execute → delivered body one occurrence behind | Med (ms window per coalesced fire) | Low (advisory content; authoritative `_defer_count` exact) | Single-statement conditional update (no read-then-write); R4 counter; pin test on the no-op path |
| **R2** | One-shot duplicate via terminal-bypass: delivery *completes* inside the enqueue→consumption window + crash + re-fire | Low (needs crash + completion in a ms window) | Med (duplicate alarm — "exactly one message" breach, visible) | Consume immediately after enqueue returns; W1-style once-per-`run_at` guard (`scheduler.py:544-556` pattern); disclose in DEC notes |
| **R3** | Generation-marker staleness: crash between enqueue-commit and marker write → orphan live delivery + stale marker | Low | Low (marker-miss fallback self-heals via dedup-hit → returned kept `message_id` → guarded update corrects body) | Enqueue-fallback path is the recovery mechanism; pin the crash-window test |
| **R4** | Terminate-after-enqueue destroys the deferred Task/MQ (terminate cascade deletes both) → recurring loss until next fire; one-shot loss | Low | Med (alarm lost — but VISIBLE now: DEFERRED row + count + WARNING, vs. today's zero trace) | R2/ADR-4 visibility; WARNING; recurring self-heal; follow-up lane for a terminate-aware hook (out of scope) |
| **R5** | Semaphore re-arm hot loop (one-shot past-due, lateness unset → `wait≈0`) | High if the sleep is forgotten | Med (CPU spin, log flood) | **Mandatory** `SCHEDULER_ERROR_RETRY_S` sleep in the re-arm branch (mirrors `:580-582`); age cap bounds total spin; pin test |

Watch items (below top-5): WAITING/WAITING_CHILDREN now receive alarms (semantic delta — pin); `is_deferred`/`is_background` misuse traps; `process_message` drift-sweep gap; G3 seam review-together (DEC-17).

---

## 15. WHAT THE DEVELOPER MUST NOT DO

1. **Never use the `send_message` tool / RAM-injection lane** (`tools/instance.py:4138`) for deferred delivery — it lands inside the live turn and is lost on turn end. Only `manager.enqueue_message_job`.
2. **Never change the repository's global mapper merge semantics** (`source/repository.py:387`) — the binding-preservation fix lives at the mapper's metadata-construction site (`mapper.py:370-373`) only.
3. **No new table, no migration, no new daemon service, no new polling loop** — see §16. The re-arm branch REUSES the existing 5s retry sleep; all bounds check at fire ticks.
4. **Never reuse the run_at-scoped key for recurring-deferral coalescing** (per-occurrence keys ⇒ N deliveries) and **never mint per-fire keys** (defeats coalescing). Key changes only at generation rollover.
5. **Never re-enqueue under a colliding key while a delivery is live** (P3: non-terminal predecessor → loser MQ deleted, kept payload frozen) and **never teardown** (cancel/delete/soft-delete) a pending deferred delivery — the duplicate-delivery race is the reason ADR-1 exists.
6. **Never mutate `message_queue` without the `status IN ('ready','retrying')` guard** **[SUPERSEDED: the narrow `status='ready'` form is superseded by the leader-ruled W9 widening — `decisions.md` DEC-18/DEC-12]** — an unguarded content update can rewrite a row mid-claim. And **always re-run the scrub helper** on any recomposed body before persisting.
7. **Never stamp `Task.is_deferred` or `is_background`** — both are anti-starvation lanes (project-wide / system-wide idle), not instance-busy lanes (DEC-14.1).
8. **Never silently drop on a bound breach** (age/count cap, E1 refusal, row-write failure) — every breach writes a visible row + WARNING; audit-row-write failure fails CLOSED (no dispatch).
9. **Never trust the tool response's `status` field** (async "starting" vs actual "running") — surface reads come from `source_configs` readback.
10. **Never re-resolve the mapper at defer/delivery time** — the pinned `instance_id` from the busy check is the target; re-resolution is the stranger-spawn surface.
11. **Never increment `_run_counter` at defer time** — it counts `_execute_run` dispatches only; the divergence is documented (ADR-8) and pinned.
12. **Never consume a one-shot before durable evidence exists** — and never consume on the semaphore-no-target branch (re-arm instead).

---

## 16. NEW TABLE / MIGRATION / SERVICE / POLLING-LOOP STATEMENT (explicit)

| Question | Answer |
|---|---|
| New table? | **NO.** Episode state rides `source_configs.config` JSONB (atomic `jsonb_set`, precedent `source/repository.py:147-196`). |
| Migration? | **NO.** `schedule_executions.status` is TEXT (NOT NULL only) → `ExecutionStatus.DEFERRED` is a Python enum member; new `SchedulingConfig` fields are pydantic-settings, not DDL; migration delta stays EMPTY (DEC-17's rollback-safe posture holds). |
| New daemon service? | **NO.** Delivery rides the existing claim lane + worker pool; resume rides the existing `safe_notify_all_pools`. |
| New polling loop? | **NO.** All checks (bounds, coalescing, re-arm) run at existing fire ticks; the re-arm branch reuses the existing `SCHEDULER_ERROR_RETRY_S` sleep; deferral delivery needs no scheduler-side poller (P2's claim lane + 3s pool poll + completion notifies). |
| New code surface (additive, allowed) | ONE repository method: guarded in-place `message_queue` content/metadata update (`WHERE message_id=:id AND status IN ('ready','retrying')`), **[SUPERSEDED: the narrow `status='ready'` form is superseded by the leader-ruled W9 widening — `decisions.md` DEC-18/DEC-12]**, in the message-queue repository, modeled on the existing `update_activity` updater, scrub applied by the caller before the call. Plus: adapter deferral helper, `ExecutionStatus.DEFERRED` member, 2 `SchedulingConfig` fields, 4 config JSON keys, ScheduleDetail/self_alarm response fields. `job_queue/repository.py` remains untouched (DEC-15 — `find_by_idempotency_key` already exists, `:688-705`). |

---

## 17. Open items routed to implementation-phase verification (mapped)

- **U1** (PAUSED-task auto-dispatch on resume) — **ANSWERED here** (§8: explicit `safe_notify_all_pools` + gate; verify in the pack with a real pause/resume).
- **U5** (background/defer gate reading) — **ANSWERED here** (DEC-14.1 affirmed; do not stamp).
- **U2** (enqueue metadata persists on Task row), **U3** (`job_id`↔`work_id` equality on the deferred path), **U4** (TTL-expired terminal-bypass branch) — remain mandatory implementation-phase verifications; U4 gains a new pin under ADR-1/ADR-2 (terminal-bypass as *safety net*: assert a generation-key enqueue against a terminal predecessor yields exactly one fresh delivery).
- **U6** (`_coalesce_wakes` constants) — superseded by ADR-5's explicit cap/age knobs (16 / 24h defaults, env-tunable).
- **Probe P2 gap** (claim boundary proven, not through a real `graph.astream`) — the e2e pack must cover a real busy-instance delivery ordering assertion.

**Gaps disclosed:** Worker A (mechanism) ran with NO injected skill (self-declared DIY; `DEGRADED — skill bank miss (data-flow-design)`) — its analysis was accepted on evidence quality (git-verified tree, verbatim quotes, anchor corrections in §"Anchor corrections"); re-dispatch was skipped as substance-complete against the commission's budget limit. No other fan-in gaps; both todo nodes closed before aggregation.
