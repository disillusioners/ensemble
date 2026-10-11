# Watch-Loss Explainer — why watched missions lost ALL terminal events

**Defect:** usage-limit retry-supersede double-silence (RCA complete, job 043d64a8).
**Code base verified:** `/home/nea/ensemble-src` @ `5cb964187` (v0.19.0 merge). All anchors below were re-verified at this commit; where the RCA's line numbers had drifted, the corrected anchors are used.
**Audience:** technical, but code-unread. Every claim carries a file:line anchor; the narrative stays plain-language.

---

## 1. What a watch is, and how it arms

When an agent wants to know how a piece of work ends, it registers a **watch** — one row in the `job_watchers` table. The row captures: who is watching (`instance_id`), which work item (`job_id` — semantically a **work_id**; the SQL foreign key was deliberately removed so task-only virtual work can be watched), and **which events** the watcher wants (`watch_events`, a JSON list) — `daemon/repositories/job_queue/watcher_models.py:73-104`.

The events list is the whole story of this defect:

- **`ALL_WATCHABLE_EVENTS`** (`watcher_models.py:34-36`) is the **10-item default**: the 5 transport terminal states (`completed / settled / failed / cancelled / dead_letter`), plus `in_progress`, plus 4 mid-flight QA events. A watch registered without an explicit event list gets exactly this — **transport-only**. It subscribes to *this work item's own lifecycle statuses*. It makes **no promise about the mission** (the whole tree of work the item belongs to).
- There is an 11th event, `mission_terminal` (`ALL_MISSION_TERMINAL_WATCHABLE_EVENTS`, `watcher_models.py:60-62`). It is **opt-in only** — the **M2** rule, ratified by you: a watcher must explicitly ask for it, and in exchange gets a stronger promise — "notify me when the *admission* is terminal AND the entire mission tree is terminal." The tool layer enforces this fail-closed: unknown event names are rejected, never silently coerced (`daemon/tools/job_queue.py:2337`).

Plain-language takeaway: **a default watch is a promise about one work item; a mission_terminal watch is a promise about the whole mission.** The defect lives in the gap between those two promises.

## 2. Who gets told, and who survives — the C1 three-bucket rule

Every status/terminal notification funnels into `notify_work_watchers` (`daemon/services/work_notifier.py`). It fetches all watch rows for the work_id and sorts each row into exactly one of three buckets (docstring `work_notifier.py:346-391`, live classifier `:689-803`):

1. **`matching_claimable`** — *every* event the row subscribed to fires **now**. The row is **claimed** by an atomic `DELETE ... RETURNING` **before** any delivery is attempted (`work_notifier.py:836-860`). This is the exactly-once guarantee: the row is removed from the database first; if two racing processes both try to deliver, only the one whose DELETE actually removed the row delivers — the loser finds zero rows and stands down. **Exactly-once on success — but the row is spent.**
2. **`matching_readonly`** — *some but not all* subscribed events fire now (multi-kind retire rule, `:711-760`). The row is delivered but **not claimed** — it survives in the database for the still-pending event.
3. **`held_for_mission`** — a row that opted into `mission_terminal` while the mission is still live (`:773-784`). Skipped entirely; survives until the mission ends.

The decisive rule for this defect is the **shape (1)** rule: a pure transport-only row fires whenever its transport status matches, and **if it fires, it is fully claimable** (`:719-723`, claimable append `:799-803`). In plain language: **a default watch is consumed by ANY transport terminal status — `completed`, `cancelled`, it doesn't matter which.**

## 3. The failure chain, step by step

Setup: a mission is running; watcher row **R** is keyed on work_id **W**, armed with the default 10 events (transport-only).

**(i) The silent cancel.** Mid-mission, the LLM proxy returns a usage-limit error. This takes a dedicated deferral lane (`_handle_usage_limit`, `daemon/services/worker_pool.py:911-956`) whose contract is written in the code itself: *"Per-attempt observables are ONE log line, nothing else: no report, no instance ERROR, no message FAILED, no hierarchy deletion, no error event"* (`worker_pool.py:923-930`). The silence is **deliberate design**, not an oversight — the generic cancel path right beside it *does* fire a watcher notify (`worker_pool.py:890-909`); the usage-limit lane is the carve-out. In-window, it calls `schedule_retry` (`worker_pool.py:996-1001`) and returns. The parent task is CANCELLED and a retry child is minted. **The watcher is told nothing.** That is silence #1.

**(ii) The F6 re-key.** `schedule_retry` wraps the `RetryTurn` transition (`daemon/repositories/task/repository.py:4854`, `:5038-5047`). Inside **one transaction**, RetryTurn cancels the parent task row, inserts the retry child, then re-keys the watchers: `UPDATE job_watchers SET job_id = :child_work_id WHERE job_id = :parent_work_id` (`daemon/services/turn_transitions.py:691-700`). Row R's *contents* are untouched — same watcher, same events — but its **key** now silently points at the retry child's work_id. The in-tree comment (`turn_transitions.py:685-690`) explains why it is built this way: the migration is atomic with the child INSERT because moving watchers *without* the child row would leave lookups returning zero rows — "silently losing notifications." **F6 was designed to prevent a silent-loss class; under retry-supersession it becomes the flip side** — it silently re-targets your watch onto a different, shorter-lived work item, and no envelope ever announces the move.

**(iii) The break point — the retry receipt settles.** The retry child is a normal task. When its receipt settles, it emits a normal transport terminal notification (`worker_pool.py:831`; dispatcher `_schedule_work_notification`, `worker_pool.py:1085-1140`). The re-keyed row R matches that status, and — because it has no `mission_terminal` subscription it cannot land in the "deliver but keep" bucket — it is **fully claimable under shape (1)** and is **CAS-DELETEd at `work_notifier.py:856-860` before delivery**. From that instant, **R does not exist anywhere. This is the point of no return.** *(The RCA cited `work_notifier.py:708-712` for this; at HEAD the semantics live in the match filter `:704-708`, shape (1) `:719-723`, and claimable append `:799-803` — substance unchanged.)*

**(iv) The mission really ends — silence #2.** When the mission reaches its true terminal, every notification surface looks for watch rows on the mission's keys. **No row exists.** The one signal the watcher actually cared about — "the mission is over" — is never emitted to them. Parent supersede silent (i) + mission terminal silent (iv) = **zero terminal events. The double-silence.**

## 4. Why nothing downstream catches it

- **Pattern (f) premature finalize.** The drift reconciler finalizes an ACTIVE JobItem whose Task reads terminal. Its quiescence gates are all *task-scoped* reads — retry child live on this instance, dependency bus pending, PENDING tasks present, age floor (`daemon/services/job_recovery_service.py:2583-2686+`, helpers `:4435/:4511/:4578`) — **not mission-tree liveness**. A mission with live descendants outside those per-task predicates can be finalized early. The in-tree comment names the cost exactly: *"notify_watchers claims/deletes ALL watchers (premature completed UP-side, lost DOWN-side)"* (f2 block, `job_recovery_service.py:≈2725-2740`) — the premature notify itself **spends** watch rows at the wrong moment.
- **Witness no-publish.** The finalize boundary (`_pattern_f_finalize_failed_terminal`, `job_recovery_service.py:4209`, docstring `:4234-4263`) performs "lock release + failed_at + terminal_reason + notify_watchers" — and **publishes no durable event row**. Durable `job_completed` rows are minted only by the observer producer on the natural path (`work_notifier.py:874-885`). The recovery surface *is* the moment, and it is spent on a topology that cannot use it.
- **U1 hooks + the 300s sweep miss by construction.** All recovery surfaces — U1 hook (a) (`daemon/services/job_feedback_observer.py:1425`, reconcile call `:1607`), U1 hook (b) no_job carrier (`daemon/services/child_reports.py:5058-5102`, call `:5091`), and the 300s `WatchReconcileSweepService` (`daemon/services/watch_reconcile_sweep.py:88`, `:336`) — funnel into one helper, `reconcile_held_watches_for_instance` (`daemon/services/job_queue_service.py:710`), which scans **rows that still exist** and re-runs them through the C1 partition so *held* mission_terminal rows get delivered when liveness flips. Two misses follow: (1) **row R was already CAS-consumed in step (iii) — there is nothing to select**; (2) even a surviving transport-only row would simply be re-consumed by the next transport fire under shape (1). **The safety net is a net for rows that held on — not a resurrection of rows already spent.**

## 5. The documented mitigation lever (why it works)

Arming the watch with `events=['mission_terminal']` changes R's classification at every step: at the retry settle (iii), the mission is still live, so the row lands in the deliver-but-**keep** bucket (`work_notifier.py:773-791`, no-claim rule `:836-843`) and **survives**; at the true mission terminal (iv), liveness flips and the row is claimed and delivered **exactly once** (`:792-803`) — with the U1 hooks and sweep as backstops. It does not fix silence #1 (the supersede stays envelope-free by design), but it guarantees the one signal that matters survives. That is why the recorded mitigation is `mission_terminal` arming, with `job_continue → watch_mission` as manual recovery.

## 6. One-paragraph summary

A default watch promises "tell me when this work item ends" — but the exactly-once machinery **deletes** the watch the first time any transport terminal fires on it. A usage-limit error mid-mission silently cancels the task and re-keys your watch onto the retry child (F6). The retry child's own completion is a transport terminal — so it **consumes** your watch. When the *mission* finally ends, your watch no longer exists, the recovery surfaces only reconcile rows that still exist, and the finalize path publishes nothing durable. Zero events reach you. The fix family all attacks the same asymmetry: **make coverage survive the retry, or make terminal evidence durable.**

---

## ANCHORS (verified @ 5cb964187)

| Claim | file:line |
|---|---|
| ALL_WATCHABLE_EVENTS = 10 items (5 terminal + in_progress + 4 QA) | `daemon/repositories/job_queue/watcher_models.py:13, :26-36` |
| ALL_MISSION_TERMINAL_WATCHABLE_EVENTS = 10 + mission_terminal (M2 opt-in) | `watcher_models.py:38-62` |
| Watcher row fields; job_id = work_id; default events | `watcher_models.py:73-104` |
| watch tool accepted_events gate (fail-closed) | `daemon/tools/job_queue.py:2303, :2337` |
| C1 three-bucket partition (docstring / classifier) | `daemon/services/work_notifier.py:346-391, :689-803` |
| Claim-first CAS (DELETE…RETURNING before enqueue) | `work_notifier.py:836-860` |
| Silent-cancel contract "ONE log line, nothing else" | `daemon/services/worker_pool.py:923-930` |
| In-window defer: schedule_retry, no watcher notify | `worker_pool.py:988-1028` |
| Bus release is in-memory only ("F6 covers the DB job_watchers") | `worker_pool.py:1015-1021` |
| F6 re-key SQL + atomicity intent comment | `daemon/services/turn_transitions.py:685-700` |
| schedule_retry wraps RetryTurn | `daemon/repositories/task/repository.py:4854, :5038-5047` |
| Retry-receipt settle notify site | `worker_pool.py:831, :1085-1140` |
| Pattern (f) task-quiesce gates (corrected span) | `daemon/services/job_recovery_service.py:2583-2686+` |
| "premature completed UP-side, lost DOWN-side" comment | `job_recovery_service.py:≈2725-2740` |
| Witness finalize = triplet, no durable event publish | `job_recovery_service.py:4209, :4234-4263` |
| Durable job_completed rows only from observer producer | `work_notifier.py:874-885` |
| U1 hooks + sweep → shared reconcile helper | `job_feedback_observer.py:1425/:1607`; `child_reports.py:5058-5102`; `watch_reconcile_sweep.py:88/:336`; `job_queue_service.py:710` |
| mission_terminal survival shapes (3)/(4) + no-claim rule | `work_notifier.py:773-803, :836-843` |
