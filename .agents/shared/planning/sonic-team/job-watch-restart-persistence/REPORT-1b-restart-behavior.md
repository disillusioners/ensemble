# REPORT-1b — Restart Behavior of Armed Watches (jwr-restart)

**Base sha:** `a1130e257a3c3bb3929455aec28cacafcbfaa1a7` (origin/latest @ 2026-11-23)
**Date:** 2026-11-23
**Commission:** job-watch-restart-persistence — deliverable 1(b), RESTART BEHAVIOR of armed watches.
**Scope:** READ-ONLY investigation. No code/config/migration edits. No daemon boot, no pytest.
**Worktree:** `/home/nea/ensemble-src-wt-jwr-restart`, branch `docs/jwr-restart`.

---

## 1. Boot path trace — watch-related boot actions

Enumerated from `daemon/api.py` lifespan (and `daemon/__main__.py`, which contains **no** watch references — all watch wiring lives in `api.py`).

### 1.1 Table creation / repository wiring (early lifespan)

- `daemon/api.py` ~1355-1373: `JobWatcher.metadata.create_all(manager.engine)` + `JobWatcherRepository(engine=manager.engine)` → `manager._watcher_repo`. Stored-watch quarantine table (Option A depth 3, fix `e8a61da3`): `StoredWatch.metadata.create_all` + `StoredWatchRepository` → `manager._stored_watch_repo`.
- No re-hydration of rows here — tables are created if missing; rows persist in PG across restarts (DB-backed).

### 1.2 Late wiring (after JobRepository / WorkResolver exist)

- `daemon/api.py` ~1680-1763: `WorkResolverService` constructed; `watcher_repo` + `work_resolver` late-wired into both worker pools, `TaskProcessor`, `StaleTaskRecovery`; `JobQueueService` gets `set_watcher_repo`, `set_stored_watch_repo`, `set_work_resolver`.

### 1.3 Startup recovery (drift patterns)

- `daemon/api.py` ~1814-1843: `JobRecoveryService(...)` with `job_queue_service` (for notify on Path 7) + `task_repository` + `stale_task_recovery` + `worker_pool` + `instance_manager`; `await job_recovery.recover_on_startup()`. Several patterns (`_pattern_f_finalize_dead/done/failed_terminal`, `_pattern_e`, `_pattern_h`) call `job_queue_service.notify_watchers` (→ `notify_work_watchers`) on finalize, so a watch row whose work gets finalized during recovery CAN fire `[JOB_EVENT]` post-boot. See §2(iii).

### 1.4 Periodic sweeps started

- `daemon/api.py` ~1931: drift reconciler task (`_periodic_drift_reconcile_loop` → `reconcile_drift_states`).
- `daemon/api.py` ~1995-2010: `EligiblePendingSweepService` (task claim backstop, not watch-specific).
- `daemon/api.py` ~2033-2095: `OrphanWatcherSweepService` (dependency-bus orphan watchers, different subsystem).
- `daemon/api.py` ~2147-2191: `WatchReconcileSweepService` (U1-SLICE) — periodic `reconcile_held_watches_for_instance(instance_id=None)` every 300s; first tick fires immediately (fast on-boot catch-up). This is the structural backstop for held `mission_terminal` rows; it also runs the stored-watch GC leg (`reconcile_stored_watches`).

### 1.5 Boot-time reconcile of terminal watches (the ONE watch-specific boot action)

- `daemon/api.py:3121`: `reconciled = await job_queue_service.reconcile_terminal_watches()` — scans active `JobWatcher` rows; for each whose resolved work is already terminal (subject to mission-live guard for `mission_terminal`-opt-in task rows), calls `notify_watchers` (fires `[JOB_EVENT]`, CAS-claims + removes the row). Logged only if `reconciled > 0`.
- Placement: after worker pool setup, question-pack rehydrate, recovery, drift reconciler, and all sweeps are wired; before DeadLetterService init. **Not wrapped in try/except** at this call site (unlike the metrics block above it).

### 1.6 Question-pack boot rehydrate (precedent) — contrast

- `daemon/api.py:1093-1111`: `await _boot_rehydrate_question_packs(manager)` (after `setup_worker_pool()`). Implementation `daemon/manager.py:6191` `_rehydrate_question_packs_at_boot`: scans `task` rows with `status='paused'` + `suspension_reason='awaiting_answer'`, reads per-instance `question_pack_payload` shadow, calls `QuestionManager.rehydrate_from_payloads`. This is a genuine **re-hydrate** (RAM state rebuilt from durable shadow).

### 1.7 Watch equivalent? NONE

- There is **no** boot re-hydrate that rebuilds an in-memory watch store. Watches are DB rows (`job_watchers`, `stored_watches`) and are read live from the DB; the only boot-time action is the terminal-reconcile scan above (§1.5) plus the periodic sweep's first tick (§1.4). `reconcile_terminal_watches` is a **replay of missed terminal notifications**, not a re-hydration of armed state.

### 1.8 Message-queue boot recovery (v0.27.0 hard-reboot lane)

- `daemon/api.py` ~1054-1062: `recover_stuck_messages_on_startup(manager._queue_repository, get_boot_epoch())` runs BEFORE `setup_worker_pool` (reset-only: flips `processing` rows to `ready`, with skip-guards for TERMINAL instances and rows backed by RUNNING/PAUSED tasks). This is what makes an already-enqueued-but-undelivered `[JOB_EVENT]` row deliverable again after a hard reboot (§3).

### 1.9 Auto-continue boot pass

- `daemon/services/auto_continue_boot_pass.py`: continues RUNNING instances from checkpoint. Does **not** touch job watches or fire `[JOB_EVENT]`; it is about resuming in-flight turns.

### 1.10 Dependency bus start (waiting_children, separate subsystem)

- `daemon/api.py:3164` `await init_dependency_bus(app, manager)`: `bus.start()` → `_warm_cache` + `_recover_fired_unsent` (FIRED-but-unstamped rows, at-least-once finalization). This is the `waiting_children`/`instance_hierarchy` subsystem, NOT the `job_watchers` `[JOB_EVENT]` subsystem. Do not conflate.

**Conclusion on boot actions for watches:** table create (idempotent) + late wiring + recovery patterns + periodic sweeps + **one** terminal-reconcile scan. No in-memory re-hydrate. No stored-watch re-hydrate at boot (stored watches are reconciled by the periodic sweep's first tick and its `reconcile_stored_watches` leg).

---

## 2. Terminal-vs-downtime matrix

For a `job_watchers` row that survives in the DB across a restart:

### (i) Target went terminal BEFORE shutdown but notification never ran — is there ANY replay?

**Yes.** `reconcile_terminal_watches()` (called at boot, `api.py:3121`) enumerates active watches, resolves each `work_id` via `WorkResolverService`, and if the resolved record is terminal calls `notify_watchers` (fires `[JOB_EVENT]`, CAS-claims + removes the row). `reconciled` counter logged if >0.

Caveat — mission-live guard: for a `mission_terminal`-opt-in watch on a task-kind job whose work row settled, the boot scan consults `_mission_live_guard_holds` (`api.py` method) and **skips** the notify if the mission is still live, leaving the row untouched for the next sweep. This prevents a premature `completed ✓` during a still-live mission. Receipt-kind (no `mission_terminal`) and non-task rows are unaffected.

Also covered by `WatchReconcileSweepService` (§1.4) post-boot and `JobFeedbackObserver._fire_watcher_notify_for_terminal` in-session fast path.

### (ii) Terminal DURING downtime — who can mutate state while the daemon is down?

**Nothing.** The daemon is the sole writer to `job_watchers`/`job_queue_items`/`task`; PG is the store but no external writer process mutates these tables while the daemon is down (no cron, no separate service). External DB connections (e.g. `ensemble_prod_ro`) are read-only operators. Therefore a work item cannot transition to terminal during downtime; its state is whatever the last committed daemon write left.

Consequence: "terminal during downtime" is not a real state transition — the row is either already-terminal (caught by (i)) or non-terminal (caught by recovery patterns / held-watch reconcile). The only mutation that CAN happen while "down" is a hard reboot where the previous process died mid-finalize; that is handled by `JobRecoveryService.recover_on_startup()` (§2(iii)) and `StaleTaskRecovery`, not by an external writer.

### (iii) Crash-recovery finalize paths post-boot — do they call `notify_work_watchers` so a surviving row fires `[JOB_EVENT]`?

**Yes, on the finalize paths that terminate a job.** Anchors:

- `JobRecoveryService._pattern_f_finalize_dead` (~4209-4240): `job_queue_service.notify_watchers(job_id, "dead_letter")` after the DEAD transition.
- `JobRecoveryService._pattern_f_finalize_done` (~4415): `notify_watchers(...)` for completed.
- `JobRecoveryService._pattern_f_finalize_failed_terminal` (~4669): `notify_watchers(...)` for failed.
- `JobRecoveryService._pattern_e` dead-letter path (~835, ~963): `notify_watchers` calls.
- `JobRecoveryService._pattern_h` zombie-active close (~5319): `notify_watchers(job_id, canonicalize_status(token))`.
- `StaleTaskRecovery` (~1022): `notify_work_watchers` fire-and-forget via `MainLoopBridge`.
- `JobFeedbackObserver` (~1564): held-watcher re-fire seam + post-commit outbox seam (U1-SLICE) calling `notify_watchers`.
- `WatchReconcileSweepService` (periodic, post-boot): `reconcile_held_watches_for_instance` → `notify_watchers` CAS-claim path; also `reconcile_stored_watches` (stored-watch rehydrate GC leg).

Drift reconciler (`_periodic_drift_reconcile_loop`, `api.py` ~1931) drives `reconcile_drift_states` which invokes the above patterns, so notify calls recur periodically.

So a surviving `job_watchers` row can fire via (a) boot reconcile (if work already terminal), (b) recovery finalize (if work gets finalized during recovery), (c) in-session observer seam, or (d) periodic held-watch reconcile sweep.

### (iv) Normal post-restart terminal — expected fire?

**Yes, expected.** Once the daemon is up and a watched work item reaches terminal through the normal worker-pool/task-processor path, `notify_work_watchers` fires `[JOB_EVENT]` to the watching instance (if running) or enqueues durably in `message_queue` (if not). The watch row is CAS-claimed and removed on a terminal match. Held `mission_terminal` rows survive until mission terminal, then fire via the observer seam / periodic sweep.

---

## 3. Enqueued-but-undelivered `[JOB_EVENT]`s across restart

### 3.1 Durability of the enqueue

`notify_work_watchers` delivers via `instance_manager.enqueue_message(..., source="internal_agent:job_event:...")`, which persists a row in the `message_queue` table. The row survives a restart.

### 3.2 Boot recovery of stranded `processing` rows

`recover_stuck_messages_on_startup` (`daemon/services/message_queue_boot_recovery.py`, called `api.py` ~1054-1062, before `setup_worker_pool`): resets rows with `status='processing'` and `last_activity_at < boot_epoch` to `ready`, with two skip-guards baked into the CAS UPDATE (`reset_stuck_message_to_ready`):

1. **TERMINAL instance skip** — rows whose instance is in `TERMINAL_INSTANCE_STATUSES` are NOT reset (revive hazard: a later revive would deliver a stale row).
2. **RUNNING/PAUSED task skip** — rows backed by a `task` with `status IN ('running','paused')` are NOT reset (AutoContinue owns those).

So an enqueued `[JOB_EVENT]` to a **non-terminal, non-paused-task** instance is reset to `ready` and redelivered when the instance runs. An enqueued `[JOB_EVENT]` to a **terminal** instance is left stranded (not redelivered) — by design, to avoid stale delivery on revive. An enqueued `[JOB_EVENT]` backed by a running/paused task is left for AutoContinue.

### 3.3 Held `mission_terminal` rows — periodic backstop

`WatchReconcileSweepService` (`daemon/services/watch_reconcile_sweep.py`, 300s interval, first tick immediate at boot) runs `reconcile_held_watches_for_instance(instance_id=None)` globally: CAS-claims + delivers held rows whose mission is now terminal; retires zombie rows (job gone + mission terminal/unresolvable). This is the post-boot backstop for held `mission_terminal` rows that the in-session seams missed. It also runs `reconcile_stored_watches` (stored-watch GC leg: rehydrate lost carriers).

### 3.4 Stored watches (quarantine, Option A)

`stored_watches` rows (status `armed`) survive restart. They are reconciled by the sweep's `reconcile_stored_watches` leg (aged > 6h), not at boot directly. No boot-time rehydrate; first sweep tick (immediate) covers them.

---

## 4. Precedent compare — question-pack boot rehydrate (v0.25.2)

- **Sites:** `daemon/api.py:1093-1111` (call) + `daemon/manager.py:6191-6280` (`_rehydrate_question_packs_at_boot`). Mechanism: scan `task` rows `status='paused'` with `suspension_reason='awaiting_answer'`, read per-instance `question_pack_payload` metadata shadow, call `QuestionManager.rehydrate_from_payloads` to rebuild the in-memory pack store. This is a true **RAM re-hydrate from a durable shadow**.

- **Watches:** **There is NO boot equivalent.** Watches are not an in-memory store; they are DB rows read live. The only boot-time watch action is the terminal-reconcile scan (`api.py:3121`) which **replays missed terminal notifications** for already-terminal work, plus the periodic sweep's immediate first tick. There is no "rebuild armed-watch RAM state" step because there is no such RAM state. Any claim that watches boot-rehydrate like question packs is false.

---

## 5. Divergence vs live — tag v0.31.42

### 5.1 Requested diff

Command: `git diff v0.31.42..HEAD --stat` over watch-path files (`daemon/tools/job_queue.py`, `daemon/services/work_notifier.py`, `daemon/services/watch_reconcile_sweep.py`, `daemon/repositories/job_queue/*`, `daemon/migrations/versions/20261011_000001*`).

**Result: empty (no material deltas).** HEAD is 4 commits ahead of `v0.31.42` (all docs-only: `a1130e257`, `38d590389`, `d700970c4`, `0731e4577`); no watch-path code changed.

### 5.2 watch-loss fix 29651c06e ancestry

Command: `git merge-base --is-ancestor 29651c06e v0.31.42; echo $?`

**Result: exit 0 → 29651c06e IS an ancestor of v0.31.42.** (Commit: `29651c06e merge: fix usage-limit retry-supersede double-silence — Option A stored-watch quarantine/re-arm + E1 mission-live finalize gate`.)

**Note / open question:** The commission brief asserted the fix is "NOT in live". The git fact says the opposite — `29651c06e` is reachable from the `v0.31.42` tag commit (`ea5325a60`). Either "live" refers to the actually-running daemon (which system context places at `releases/v0.28.16`, not v0.31.42 — could not verify running version without booting, forbidden by fence) or the brief's premise is stale. **Could not verify the running-daemon version** (fence: never boot). Flagged for Emperor decision input.

### 5.3 Anchor drift (brief → actual)

| Brief anchor | Actual at base `a1130e257a` |
|---|---|
| `api.py:2456` (boot-time twin) | `api.py:3121` (`reconcile_terminal_watches()` call) — **drifted** |
| `api.py` loop `:3942-3948` | Could not map to a single reconcile call; `api.py:3942` is the engine boot block. The periodic reconcile loop is `_periodic_drift_reconcile_loop` (~5262). **Drifted / unverifiable as cited.** |
| `job_recovery_service.py` pattern-f finalize `≈:4209` | `_pattern_f_finalize_dead` notify at ~4228; `_pattern_f_finalize_done` at ~4415; `_pattern_f_finalize_failed_terminal` at ~4669. **Approximately correct (4209 → 4228).** |
| `watch_reconcile_sweep.py:88,:336` | File exists; `:88` is in the module docstring; `:336` is mid-`__init__` (heartbeat cadence comment). The sweep's `sweep_once` / `reconcile_stored_watches` leg are at ~line 160-230. **Drifted.** |
| `migrations/20261011_000001*` | Present (stored watches). |

---

## 6. Open questions for Emperor decision input

1. **Ancestry contradiction:** git says `29651c06e` is in `v0.31.42`, but the brief says it is NOT in live. Is "live" = running daemon (possibly `v0.28.16` per system context) rather than the tag? Could not verify running version (fence: no boot). Needs operator confirmation.
2. **No watch boot re-hydrate:** Watches have no question-pack-style re-hydrate. If the Emperor expected armed-watch RAM state to be rebuilt at boot, that capability does not exist; only terminal-replay + held-row periodic reconcile. Is a true boot re-hydrate desired?
3. **Terminal-instance stranded `[JOB_EVENT]`:** Enqueued-but-undelivered `[JOB_EVENT]` rows to a terminal instance are deliberately NOT reset (revive hazard). If a watcher's target instance terminated before delivery, the `[JOB_EVENT]` is silently dropped across restart. Is that the intended durability contract?
4. **Unwrapped call:** `reconcile_terminal_watches()` at `api.py:3121` is not wrapped in try/except; an exception aborts boot. Other boot recoveries are fail-soft. Intentional or latent?
5. **Stored-watch boot coverage:** Stored watches are reconciled only by the periodic sweep's first (immediate) tick, not a synchronous boot call. A daemon that boots and immediately receives traffic before the first tick (immediate, but async) could miss rehydrate. Acceptable?

---

## Summary

- **Boot actions for watches:** table create (idempotent) → late wiring → `JobRecoveryService.recover_on_startup()` (finalize paths notify) → periodic sweeps (drift, eligible, orphan, watch-reconcile, job-lock) → **`reconcile_terminal_watches()` at `api.py:3121`** (terminal replay) → message-queue boot recovery (reset stranded `processing` rows) → auto-continue pass (instances, not watches) → dependency bus start (separate subsystem).
- **No in-memory re-hydrate** for watches (contrast: question packs).
- **Terminal replay exists** for already-terminal work; mission-live guard holds `mission_terminal` rows.
- **Enqueued `[JOB_EVENT]` rows** survive in `message_queue`; reset to `ready` on boot unless instance terminal or task running/paused.
- **Periodic held-watch reconcile** (300s, first tick immediate) is the post-boot backstop.
- **v0.31.42 diff:** empty on watch paths. `29651c06e` IS an ancestor of `v0.31.42` (exit 0) — contradicts brief premise.
