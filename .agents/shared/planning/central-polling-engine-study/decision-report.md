# Decision Report — Central Polling/Event-Management Engine (Phase 0, investigation-only)

**Date:** 2026-11-21 · **Lane:** architect synthesis (Phase 0 decision study) · **Status:** decision-ready for Emperor architecture discussion · **Rev:** 2 (post-review re-base, see §F.2)
**Synthesis inputs:** `census-periodic.md`, `census-events.md`, `liveness-evidence.md` (this directory) + 4 dispatched analysis lanes (§F.2)
**Deliverable boundary honored:** this file is the ONLY artifact written by this lane. No code, no commits.

> ⚠ **VERSION-SKEW CAVEAT (two-tier citations after the Rev-2 re-base).** The two repo censuses read the canonical checkout at detached HEAD `bf4bfde6e` (v0.30.9-era, read 2026-11-21); **live runs v0.31.22**; the chatroom subsystem (event sources EV-47..EV-51) exists **only on `origin/latest` `0b5624dc9`** (GAP-01). Citation tiers: **[C]** = census code-truth at `bf4bfde6e`; **[L]** = reviewer-verified against the **v0.31.22 tag** during the Rev-2 review (load-bearing live-defect claims only). Material skew flagged inline: ram-guard cadence is 200ms in the census tree (F8 ⚠; commission said "~1s" — newer tree, live override, or the in-flight L1 lane); metrics census sampler is code-default-ON at `bf4bfde6e` but DORMANT-by-flag on live (`census_enabled=false`, liveness row 14). **Rev-2 re-base (reviewer-verified [L]):** two pillars the censuses presented as live defects are **already fixed at v0.31.22** — the maintenance starvation (v0.31.11 composite fix: 24h max-staleness escape hatch + job_metrics skip telemetry) and GAP-02/03 stranded watchers (S1 `fire_for_terminal_child` landed AND wired). Pillars verified INTACT at v0.31.22: born-dead OrphanWatcher (api.py:1920 capture precedes :2972 init → :5710 set), PEXIT loops (job_lock_sweep.py:241 catch → :250 return), dead outbox (consumers still DEBUG-only: instance_lifecycle.py:5976/:6589), root-only stream (event_publisher.py:91 `if parent_id is None:`), out-of-tree nudge mint (instance_ops.py:711, docstring :596). House constraints "<1 core" and "off-loop discipline v0.27.14" are **commission-supplied house facts** (Emperor commission), not census measurements. Row counts from the census artifacts: **55 periodic mechanisms / 13 families + 14 boot-only passes**; **54 event rows** (EV-55 bundles five) **+ 20 gaps**; **33 liveness verdict rows** (21 EXECUTES incl. 1 thin / 3 BOOT-ONLY / 2 DORMANT-TRAPPED / 1 DORMANT-by-flag / 5 INSUFFICIENT-EVIDENCE — observed in a 5.5h post-boot window; steady-state reading in §E.5).

---

## 0. Executive Summary (the verdict, plainly)

**The answer to "do we need a central engine?" is: not the engine as framed — and not nothing.** Every analysis lane and both council models independently reject the maximal central engine (Option A) as the worst option on the table: a single abstraction forced over 4–6 topology shapes that exist for reasons (census §10), with thread-death-as-signal mechanisms (ram-guard 200ms, wedge watchdog 1Hz "NEVER a coroutine") that a scheduler-hosted rewrite would violate by contract. But the evidence also rejects "do nothing": the live system carries named, recurring, structural failures — a sweeper **born dead on every boot** for ≥2 boots while its counters climb (OrphanWatcher, liveness row 3; wiring trap verified intact at v0.31.22 **[L]**: api.py:1920 capture precedes :2972 init → :5710 set) and a reaper **silently stopped at 02:57:58Z** with 3,604 spawns unreaped and a 194k-row registry growing unbounded (row 31) — plus a diagnostic cost so high it took a 3-worker census + live forensics just to learn what runs. The maintenance tier's 6.54-day starvation (43.2 GB cleared only by a promote boot) is **HISTORICAL — v0.31.10, fixed by the v0.31.11 composite** (max_staleness escape hatch, maintenance.py:501 `forced = gated and self._is_stale(job)` / :210, plus job_metrics skip telemetry: `last_success_ts`, `consecutive_skips`) **[L]**; live verification of that fix and observability convergence remain open (§C.0).

**Recommendation (§E): a gated hybrid.**
1. **NOW, unconditionally (the "D" content):** two live-defect fixes — **OrphanWatcher late-bind**, **bash-reaper retention coverage** — plus one verification/convergence task: confirm the landed v0.31.11 maintenance fix behaves on live and converge observability onto its `job_metrics`. (The maintenance honest-state repair itself already landed — the Rev-2 re-base found it.) These must not be held hostage to an architecture program.
2. **NOW, as Phase 0 (the "C" layer):** a scheduling-**primitives library** (single-flight, jitter, max-runtime supervisor, honest-state helper that stamps `last_run` on run AND skip with reason, boot-time dependency assertion) + observability **converged onto the existing `job_metrics` family** (extend with `next_due / last_duration / last_outcome / dead-flag` — no parallel family). This is the Emperor's "build the engine first but don't use yet" instinct, made honest: the observability family **is the registry in disguise** — the registry view (what runs, what skips, what died, why) without owning any execution. Every primitive maps 1:1 to a census defect class; nothing speculative.
3. **GATED (the "B" subset engine):** the subset engine is **fully designed in §B.2** and **pre-approved to build only when measured evidence triggers the gate** (§E.3 two-sided falsification protocol). Nothing adopted under the C layer is thrown away — the engine, if built, uses the primitives as internals.
4. **PERMANENTLY STANDALONE:** ram-guard, wedge/loop-lag detectors, readiness/livez, the scheduler substrate (DST domain), and heartbeats where thread-death IS the signal (§A.6).
5. **OUT OF SCOPE regardless:** wakeup-churn consolidation (4.6%/3.3%) — a worker-pool claim-path problem, not a sweep problem (§B.4).

**The re-base makes C+D cheaper, not dearer:** two of the census's biggest pillars (maintenance starvation, GAP-02/03 stranded watchers) were closed by ordinary targeted fixes with **no engine** — empirical support for the point-fix path (§D.1). The Emperor's phased instinct is **ratified**; the maximal framing and a general "trigger on which event" registration API are **not v1-realistic** (§B.3: ~4–6 trigger classes over substrates that exist — terminalization now hookable **today** via the landed S1 fire — not generality).

---

## A. Taxonomy & Pain Map

### A.1 Families (55 periodic mechanisms + 14 boot passes, regrouped by scheduling class)

| # | Family (census rows) | Cadence / substrate | Verdict |
|---|---|---|---|
| 1 | **Reconcilers** — WatchReconcile #2, EligiblePending #3, OrphanWatcher #4†, DriftReconcile #6, RDRS #8, MissionStrandHealer #9, StaleTaskRecovery #32, StalePause #33 | 60–300s, main-loop asyncio tasks | ENGINE-ELIGIBLE |
| 2 | **Recovery sweeps** — JobLockSweep #1 + boot passes #18/#21/#24/#27–#31 | boot one-shot + 90s periodic | ENGINE-ELIGIBLE (boot-hooks + JobLock periodic) |
| 3 | **Housekeeping** — maintenance tier #12–#16, SourceCleanup #37, SourcesGc #38, manager cleanups #56/#57, TmpImage #58, PlaneSyncWatchdog #59, UpgradeJournalSweep, ServiceReconciliation | 1–24h, AL (+to_thread DB) | ENGINE-ELIGIBLE |
| 4 | **Telemetry ticks** — resource sampler/flusher #39/#40, metrics census #44 (flag-off live), metrics sampler #45, checkpoint-metrics #46, log_sumup #47, F13 sampler | 10–60s, mostly threads | ENGINE-ELIGIBLE |
| 5 | **Memoir workers** #54a/b/c (60s/30s/20s) | AL ticks, LLM-budgeted (45s/tick, lane-down latch) | ENGINE-ELIGIBLE (borderline — carry latch + scoring-thread split) |
| 6 | **Per-entity conditional loops** — scheduler sources #35, adapter supervisor #36, TaskHeartbeat #34, vscode health #51, FileChangeMonitor #60, compact heartbeat #61, promote_txn_hold #62, mcps_gateway | event/cond, per-entity | CONDITIONAL (domain-coupled) |
| 7 | **Cadence-gated piggybacks** — anomaly-on-flusher, checkpoint-metrics-on-increment, queue_freshness+sentinel-on-readiness, bash reaper #55 | host cadence, no own loop | ENGINE-ELIGIBLE (inherit host) |
| 8 | **Probes** — readiness refresher #50, MCP health #52, vscode #51, queue-freshness, instance-status sampler | 2–10s, external contract | STANDALONE |
| 9 | **Real-time guards** — RamGuard #41 (200ms TH×2), WedgeCapture #42 (1Hz TH), LLM stream watchdog #48 (1s TH), long_tool_nudge #49 | off-loop threads | STANDALONE |
| 10 | **Boot-only passes** — F5 #17–#31 (14) | one-shot | BOOT-HOOKS (not periodic; engine may orchestrate the boot phase only) |

† DORMANT-TRAPPED on live (born-dead wiring trap, §A.6; verified intact at v0.31.22 **[L]**).

The event machinery (census-events: 54 rows, EV-01..EV-55) is **not an engine job set** — it is the **trigger substrate** the engine would consume (terminalization emits, enqueue seam EV-24, admission transitions EV-26, nudge/watcher rows, attestation ledger, EventBus, commissions/report-injections, room lanes on latest).

### A.2 Overlaps (redundant/conflicting sweeps on shared state)

1. **Instance-strand cluster (7 paths, the star):** OrphanWatcher #4 (trapped), MissionStrandHealer #9, WaitingChildrenWatchdog #10, RDRS lanes 5+7 (#8), DriftReconcile zombie leg #6, AutoContinue #28 + sweep_orphaned_live_turns #29, DependencyBus.start() one-shot sweep #26 — all touching `instances` (status/parent_id), `dependency_watchers`, `report_injections`, `mission_commissions`, `tasks`. Split-brain risk: the only path that would cover inter-boot watcher orphans (#4) is the one that is dead. False-positive interplay: WC-watchdog `last_activity_at`-freeze (stale leader 68f04ccd, 3.04d idle, flagged by nothing that acts); heal invariant conjunctive fail-closed at 7200s age bound; RDRS FAIL-MISS holds on probe error.
2. **Stuck-work cluster:** JobLockSweep #1, DriftReconcile #6, StaleTaskRecovery #32, EligiblePending #3, RDRS lane-4 — shared `job_queue_items`/`tasks`/`job_locks`. JobLockSweep produces **zero durable evidence when healthy** (rowcount log on reclaim only, A3) — "healthy = invisible."
3. **Watcher/backstop cluster:** WatchReconcile #2 (the one healthy periodic backstop, 300s exact cadence, 613 watchers fired), trapped #4, bus boot sweep #26, bash reaper piggyback #55.
4. **Maintenance tier audit asymmetry (softened by Rev-2):** at liveness-sample time only checkpoint-cleanup + vacuum had ever written `maintenance_runs` rows (2 sections / 118 rows since 09-27), and ~8 jobs were unverifiable **through the channels sampled** (in-memory `last_run`, no rows, no logs in window — liveness row 7). The v0.31.11 composite added skip telemetry (`job_metrics`: `last_success_ts`, `consecutive_skips`) **[L]** — the remaining gap is surfacing/converging on it, not missing machinery.

### A.3 Gaps (should have a check/event; doesn't)

- **CLOSED at v0.31.22 (Rev-2 re-base [L]) — GAP-02/03 stranded watchers:** the census-confirmed defect (COMPLETED/ERROR/FAILED lacked a target-agnostic child-keyed fire; only TERMINATED had it; the out-of-tree nudge mint at instance_ops.py:711, docstring :596, was the stranded victim class) is **fixed at live**: S1 `fire_for_terminal_child` landed and wired — dependency_bus.py:1921 (marker comment names it), wired at child_reports.py:6444/6643/6945 + error_reporting.py:693. No repair work remains; B.3's terminalization trigger is therefore hookable **today**.
- **Named-defect gap (verified intact [L]):** GAP-04 global notification stream root-only — `event_publisher.py:91` `if parent_id is None:`; non-root and job-level completions still never reach the global stream (distinct emitter path vs per-job SSE/event rows).
- **Substrate gaps (census [C], unchanged):** GAP-13 designed outbox DEAD (`turn_transitions.py:32-42` — trigger taxonomy exists; consumers still DEBUG-only at v0.31.22: instance_lifecycle.py:5976/:6589 **[L]**); GAP-14 zero queue-depth plumbing (all depth signals pull-only — "run X when depth>N" has nothing to hook); GAP-15 six incompatible fan-out primitives (EventBus / DispatchEventBus / LiveEventHub / Broadcaster / dependency_bus / lifecycle_hooks — every one a different loss policy); GAP-16 uneven kill switches (SSE lanes, lifecycle hooks, drift publisher, report-integrity guard, eligible-pending, orphan-watcher have NO off-switch).
- **Census negatives:** bash-spawn retention prune is **boot-only** → inter-boot unbounded growth (live: 194k rows / 70 MB) + reaper silent-stop (row 31); inter-boot watcher orphans have no sweeper (the trapped one — the 2.39-day husk 9c985fc7 class is an OrphanWatcher-coverage fact, independent of the now-closed GAP-02/03); GAP-05 observer asymmetry ({TERMINATED,FAILED} re-fire only); GAP-06 no `instance_terminal` kind; GAP-07 phantom delivery stamp; GAP-08 claim-crash loss window; GAP-09 nudge-mint fail-open (4 miss lanes); GAP-10 admission transitions silent; GAP-11 rate-limit/circuit-breaker invisible; GAP-12 attestation dark; GAP-17 RAM-only state; GAP-18 no replay on live planes; GAP-19 QA scope split; GAP-20 no inbound per-user lock; GAP-01 checkout skew (chatroom latest-only).

### A.4 Collisions

- **300s phase-lock club (six services):** StalePause (TH), RDRS (TH), DriftReconcile (+30s — the one deliberate breaker), WatchReconcile, MissionStrandHealer, PlaneSyncWatchdog — re-coincide on the DB every 5 min; WC-watchdog (3600s) lands on a 300s multiple every 12th tick (census §6). **No contention metrics exist to prove this hurts** — material to §E's gating logic.
- **Boot thundering herd:** ~11 awaited serialized passes (maintenance boot sweep, MQ reset, lease recovery, question-pack rehydrate, job-lock recovery, job recovery, snapshot sweep, bus FIRED-unsent, upgrade-journal reconcile, AutoContinue+orphan sweep, stale-pause + service-recon) extending boot wall-clock + 20+ first-tick-immediate services; fire-and-forget: bash reconcile, RDRS boot sweep, delete-doomed (readyz-gated).
- **Contention points:** maintenance idle-gate full-system scans (`has_active_non_deferred_work` + `list_all_pending`+`find_processing_jobs` + request-registry — every 15 min); refcounted per-root heal locks (#9 vs event path); `MaintenanceRunLock` + `uq_maintenance_runs_running_section`; `job_watchers` atomic DELETE-claim; `dependency_watchers` guarded CAS; per-source locks; checkpoint-cleanup 43.2 GB/96k blobs in 58 min (heavy write burst); vacuum 10-min stmt timeout.

### A.5 Per-family pain × simplicity (migration ordering input)

| Rank | Target | Pain (evidence) | Simplicity |
|---|---|---|---|
| 1 | OrphanWatcher #4 wiring fix | HIGH — trapped at live (verified [L]), live uncovered husk class | HIGH (small file, lazy-getter) |
| 2 | Bash reaper retention | HIGH — 194k rows unbounded, silent stop at live | MED (add periodic prune lane) |
| 3 | Maintenance tier #12 | HISTORICAL-HIGH — 6.54d gap on v0.31.10; **fix landed v0.31.11** (max_staleness + job_metrics) [L]; remaining = live verification + observability convergence | verification: HIGH; full-tier migration: LOW |
| 4 | JobLockSweep #1 | MED — PEXIT no-supervisor (verified intact [L]: :241 catch → :250 return), no healthy trace | HIGH |
| 5 | RDRS #8 | MED — proto-event-engine, 7+1 lanes | LOW-MED |
| 6 | StrandHealer #9 / DriftReconcile #6 | MED | MED |
| 7 | EligiblePending #3, StaleTask #32, StalePause #33, WatchReconcile #2, SourcesGc #38, TmpImage #58, PlaneSync #59, SourceCleanup #37 | LOW (healthy) | HIGH |
| 8 | Telemetry + memoir + housekeeping tail | LOW | VARIES |

### A.6 Standalone adjudications (the five named candidates — all lanes converged)

1. **Ram-guard real-time poller — MUST STAY STANDALONE.** 200ms TH×2 (at `bf4bfde6e` [C]; L1 may change), one-shot PSS confirm with 250ms deadline, kill ladder TERM→grace→KILL; must survive a starved event loop, therefore cannot live on it (census #41; live captures 03:16/05:48/07:23).
2. **Wedge/loop-lag detectors — MUST STAY STANDALONE.** 1Hz TH, "NEVER a coroutine" (watchdog.py:7-13 [C]); a loop-scheduled detector cannot detect loop starvation (census #42; live: 2 sustained_loop_lag captures 12.12s/20.57s).
3. **Readiness/livez probes — MUST STAY STANDALONE.** 10s external contract, sole DB toucher for /readyz, SELECT 1 (500ms budget), sentinel write, stale-composite-serves fail-open; engine coupling turns an engine wedge into a readiness lie (census #50; live /readyz ready, reasons=[]).
4. **Memoir workers — ENGINE-ELIGIBLE (borderline).** No external latency contract; AL ticks; idempotent; LLM-bound but budgeted (45s/tick, lane-down latch 3-fail→300s, two scoring threads so LLM overrun can't block accounting — liveness rows 23–25 EXECUTES). Migrate the latch + thread split + concurrency cap WITH the job.
5. **Scheduler substrate — MUST STAY STANDALONE (domain-coupled).** DST-correctness is its own domain (croniter defects, re-anchoring, 5-rung tz resolution), per-source tasks under the adapter supervisor (restart backoff 2s→300s), one-shot re-arm 5s, deferred-delivery latch, lateness-cap skip semantics (census #35/#36; Scheduled-Tasks blueprint). An engine may orchestrate its lifecycle; it must not own DST logic.

---

## B. Design Proposal

### B.0 Shape decision (preview; full reasoning in §D/§E)

The census supports building **the C layer now** (primitives + observability convergence) and **holding the B subset engine behind a measured gate**. §B.1 specifies what ships now; §B.2 is the complete engine design (pre-approved artifact for the gate); §B.3 is the v1 event surface either way. Provenance note: the "<1 core" CPU budget and the "off-loop discipline v0.27.14" topology constraint are **commission-supplied house constraints**, honored throughout — not census measurements.

### B.1 Build-now layer: scheduling-primitives library + observability convergence

**Library primitives** (each maps to a defect class — one of them now has a landed precedent, see below):
- `honest_state` helper — stamps `last_run_at` + outcome on **run AND skip**, skip reason enum (`IDLE_GATED, KILL_SWITCHED, DEP_MISSING, ERROR_BACKOFF, OVERLAP, TIMEOUT`). The permanently-due trap (maintenance.py:260-269 [C]: skip `continue`s before the stamp at :269 → due forever; ~500 skip logs, 0 executions on v0.31.10) was **fixed in exactly this shape by the v0.31.11 composite** (stamp/skip telemetry in `job_metrics` + max_staleness escape) **[L]** — the helper's remaining job is to **generalize that landed pattern to every adopter**, not to re-invent it.
- `supervised()` / max-runtime wrapper — bounds `await job.execute_fn()` (maintenance.py:267 [C] had no timeout; a wedged job blocked the whole tier — the v0.31.11 composite should be checked for a runtime bound during live verification), and converts loop-level unexpected-Exception PERMANENT EXITS (Template-B: JobLockSweep — verified intact at v0.31.22 **[L]**, :241 catch → :250 return; SourcesGc `:256-262`, TmpImage `:343-348`, upgrade-journal `:3077-3083` [C, class verified intact]) into supervised restart with backoff.
- `resolve_at_first_tick()` dependency getter + **boot-time wiring assertion** — dependencies resolved by calling the singleton getter at tick time, never captured in `__init__` (OrphanWatcher trap, verified intact at v0.31.22 **[L]**: api.py:1920 capture precedes :2972 init → :5710 set → born dead **every boot**); assertion fails LOUD at boot, not DEBUG-quiet forever.
- single-flight lock helper (generalizes `MaintenanceRunLock`, maintenance_run_lock.py:52 [C]).
- jitter helper — desynchronizes the 300s club at adoption time.
- Silent-no-op visibility: any skip/no-op path emits a counter, never just a DEBUG line (bash reaper's kill-switch-OFF = zero-access zero-log no-op, A2 [C]).

**Observability — CONVERGE, don't mint (Rev-2):** the v0.31.11 composite already landed per-job skip telemetry in **`job_metrics`** (`last_success_ts`, `consecutive_skips`) **[L]**. The C layer extends THAT family — add `next_due`, `last_duration_seconds`, `last_outcome`, `skips_total{reason}` label parity, and a liveness flag (`dead=true` when zero runs in 2× interval) — rather than proposing a parallel family. Would have exposed both headliners: the (historical) maintenance starvation as `consecutive_skips`/`last_success_ts` divergence over 6.54 days; OrphanWatcher born-dead as `runs_total=0`/`dead=true` while its own tick counter climbed. This family **is the Emperor's registry in disguise** — the registry view without owning execution, and the honest Phase-0 dormant posture.

**Adoption is per-module wrap/unwrap (the one-flag revert under C).** Library + family extension land as their own small modules; adopters wrap their existing loops — no loop is deleted.

### B.2 The gated subset engine (complete design, build only on gate trigger)

**Registration API.** `engine.register(JobSpec)`: `{id, trigger: Interval(seconds, jitter, first_tick_immediate|first_tick_offset) | Event(kind, filter), max_runtime_s, timeout_policy: cancel|extend, retry{max, backoff_base, backoff_cap}, priority 0-9, concurrency_class: MAIN_LOOP | DB_OFFLOAD(to_thread) | DEDICATED_EXECUTOR, kill_switch_key?, dependencies:[singleton getters]}`. Validation at registration: exactly one trigger; max_runtime>0. Stored in `engine_jobs` (DB) + RAM cache; re-registration upserts by id (dynamic re-registration: update interval/unregister at runtime, version bump, cancel in-flight tick, re-arm — no restart).

**Execution model.** Honors the off-loop discipline (v0.27.14, commission-supplied house constraint): DB-touching jobs via `asyncio.to_thread` (DB_OFFLOAD); CPU-bound to a small dedicated executor; main-loop class only for pure in-memory jobs. Single engine task owns the tick loop. CPU budget: the engine adds scheduling overhead only — no meaningful CPU (house <1-core finding, commission-supplied); **it must not be sold as an efficiency play** (council, both models).

**Single-flight.** Per-job `asyncio.Lock` keyed by job id, acquired on the main loop before dispatch, held across executor threads, released in `finally`. Overlap → `SKIPPED_OVERLAP` with reason (not queued).

**Honest state machine.** `IDLE → DUE → RUNNING → {SUCCEEDED, SKIPPED_<reason>, FAILED_BACKOFF, TIMED_OUT}` — **every terminal transition stamps** `last_run_at`/`last_outcome`/`last_skip_reason` (same contract the landed `job_metrics` telemetry already implements for the maintenance tier [L] — the engine generalizes it). Persistence: **DB source-of-truth** (`engine_runs` append-only + `engine_jobs` mutable, projected into the job_metrics family), RAM cache for hot reads — RAM-only is how OrphanWatcher's lie survived restarts. Backoff on consecutive idle-skips prevents skip-storms.

**Trap-impossibility (mechanisms, not intentions).**
- *Permanently-due:* stamp-on-skip + due requires `now − last_run_at ≥ interval` regardless of outcome — due-state cannot strand. (The v0.31.11 composite is the landed precedent for the maintenance tier [L]; the engine/library extends it estate-wide. Note the composite's max_staleness escape hatch intentionally re-allows a forced run after 24h — that policy rides along, now visible as a counted skip→forced-run transition instead of an invisible one.)
- *Born-dead:* dependencies resolved at first tick via getter (never construction capture) + boot wiring assertion + the meta-liveness flag (`dead=true` after 2×interval with zero runs).

**Kill switches.** Per-job `kill_switch_key` (env/config, `sources_gc_enabled` pattern) + engine-wide `ENSEMBLE_ENGINE_ENABLED` + `ENSEMBLE_ENGINE_SHADOW_MODE`. Interaction with revert flags: `SERVICES_<MODULE>_USE_ENGINE` OFF → engine unregisters the job, module's legacy loop starts; both paths compiled, flag selects — **rollback is one flag, not a code change**.

**Boot ordering.** Boot-phase classes: `BOOT_SWEEP` (awaited, deadlined), `BOOT_PASS` (awaited best-effort), `FIRST_TICK_IMMEDIATE`, `FIRST_TICK_DELAYED(+offset)`. Per-job first-tick jitter desynchronizes the 300s club; drift's +30s becomes a first-class `first_tick_offset_s`. The ~11 awaited passes stay serialized but each gets a deadline.

**Config continuity.** Existing `SERVICES_*` knobs map 1:1 into `engine.jobs.<id>.interval_seconds` with defaults preserved — migration is NOT a config break (watch_reconcile, mission_strand_heal, report_delivery_recovery, sources_gc, eligible_pending, orphan_watcher, job_lock, tmp_image, plane_sync_watchdog, stale_pause, waiting_children_watchdog, drift_reconcile interval knobs; census §1 rows 1-6, 8-10, 33, 38, 58-59 [C]). **Dead-knob cleanup candidates (census §8 [C]):** `lease_heartbeat_interval_seconds` (config.py:3158), `checkpoint_interval` (:1286), `worker_poll_interval` (:2086) — zero consumers; plus two misfiled non-periodic knobs (`min_interval_seconds` :3372, `observer_health_check_interval_seconds` :3251) that should be re-labeled, not migrated.

**Scope (subset):** families 1–5 + 7 of §A.1 (reconcilers, recovery periodic, housekeeping, telemetry, memoir, piggybacks). Families 8–9 + scheduler substrate stay standalone **permanently** (§A.6).

### B.3 Event-trigger surface v1 — a fixed catalog, not a general API

Substrate reality still forbids generality (GAP-13 outbox dead — consumers DEBUG-only at v0.31.22, instance_lifecycle.py:5976/:6589 [L]; GAP-14 nothing to hook; GAP-06 no `instance_terminal` kind; GAP-15 envelope fragmentation; chatroom kinds latest-only). **v1-realistic catalog (~4–6 classes) over substrates that exist:**
1. **Terminalization — hookable TODAY (Rev-2):** the S1 `fire_for_terminal_child` fix is landed AND wired at v0.31.22 **[L]** (dependency_bus.py:1921; wired child_reports.py:6444/6643/6945 + error_reporting.py:693). Remaining v1 work is consumer-side only: subscriptions over `dependency_watchers` CAS + the `[JOB_EVENT]` envelope (exactly-once, work_notifier.py:275-313 [C]). *(The Rev-1 "prerequisite emitter fix" is removed — already done at live.)*
2. **Durable EventBus subscriptions** — `EventBus.create_event` (event_bus.py:155 [C]; durable row + global subscribers, JobFeedbackObserver proves the pattern).
3. **State-table triggers** — formalize the RDRS pattern: `report_injections` PENDING obligations + `mission_commissions` state transitions (durable, stamped).
4. **Attestation epochs** — ledger projection as a trigger class (durable, currently fail-open).
5. *(Deferred, split ruling)* **turn_transitions→EventBus plant** — cheapest new plant point (names already defined); coding-councilor defers to v2 (hottest path in the daemon), agentic-councilor would plant now as a standalone repair. Either way: **not v1-blocking**.
- **Queue-depth triggers: cannot be delivered in v1** (GAP-14 — the data exists as SQL counts; the plumbing — poller, emitter, threshold config, metrics family — does not). Name the seam; build it only when a consumer exists.
- **Mandatory per-class failure posture, labeled not inherited:** FAIL-MISS (commissions/RDRS) vs fail-OPEN (attestation, report-integrity) vs drop-counted (RAM-guard) (census-events :163 [C]).

### B.4 Wakeup churn — explicitly OUT of scope

The 4.6%/3.3% wakeup efficiency (29,606 timeout-wakeups vs 613 notifications in 5.5h, liveness #5) is a property of **worker-pool claim loops** (`job_processor.py:679-700` wait_for_job(timeout=30) over the DispatchEventBus latch, EV-25 [C]), not the sweep estate. Unanimous across lanes: separate commission (notification-latching + adaptive timeout backoff in the pool); the engine decision must not be mortgaged to this metric. Absolute cost today is small (~1.5 empty claims/sec — latency/wakeups, not throughput).

### B.5 L1 package-lane dependencies

This study is docs-only (zero code collision). Design dependencies to carry forward: ram-guard cadence is 200ms at `bf4bfde6e` [C] and L1 may change it — **nothing in this design may hardcode it** (read config at registration/tick; ram-guard stays standalone regardless). orjson/gzip/logging changes touch only run-log serialization shape of the future `engine_runs`/job_metrics extension. If L1 lands a supervisor primitive, the gate arithmetic in §E.3 shifts (Risk axis of Option B drops).

---

## C. Phased Migration Plan

### C.0 Immediate wave — two live-defect fixes + one verification/convergence task (NOT migrations)

1. **OrphanWatcher late-bind fix (live defect, verified [L]):** resolve the bus at first tick (getter, not construction capture at api.py:1920); keep a boot assertion after `init_dependency_bus` (:2972→:5710). Restores the only would-be inter-boot watcher-orphan coverage.
2. **Bash-reaper retention coverage (live defect):** periodic prune lane for `bash_spawns` (retention prune currently boot-only — A2 [C]), so the registry cannot grow 194k rows between boots; wire the no-op paths to counters.
3. **Maintenance — VERIFY the landed fix, then converge (Rev-2):** the honest-state repair is **already in live** (v0.31.11 composite: max_staleness escape hatch at maintenance.py:501/:210 + `job_metrics` skip telemetry [L]). Remaining work: (i) live verification that max_staleness actually forces runs under real load (the 6.54-day gap was measured on v0.31.10; if the escape hatch never fires on v0.31.22+, this re-enters the queue as a repair); (ii) converge the observability family onto `job_metrics` (§B.1); (iii) confirm a max-runtime bound exists for `execute_fn` (the v0.31.11 gap list should be re-checked).
Each lands as its own small, revertable change per house standard. **Re-base lesson worth naming:** the two biggest census pillars were closed by ordinary targeted fixes with no engine — this is the empirical pedigree of the C+D path, and it also *removes* the "engine as repair" temptation: the engine question stands on architecture merit, not emergency.

### C.1 Phase 0 — dormant shadow (the "build but don't use yet" instinct, made honest)

**What ships:** the C layer (§B.1) wrapped around *observed* mechanisms with **execution still owned by each module** — the converged job_metrics family records, per job: `would_fire_at / actually_fired / skip_reason / duration / dead-flag`, plus dependency-assertion results at boot.
**What it would have proven on the live system:** the (historical) maintenance starvation would now be visible as `consecutive_skips` climbing against a frozen `last_success_ts` for 6.54 days — the landed v0.31.11 telemetry delivers exactly this signal for the maintenance tier, and convergence extends it to every adopter; OrphanWatcher born-dead visible as assertion-failure + `dead=true` while its own counter climbed (still true at live [L]).
**Honesty note (council):** a dormant shadow's evidentiary ceiling is low for *tick fidelity* — live data already shows existing loops faithful (watch_reconcile 65/65 cycles at exactly 300s; loop-lag p95 1.62s; the census's four persistent topology shapes §10 run steady [C]). The ONE real Phase-0 test is whether the registration/dependency-contract **model** surfaces false-healthy/wiring-order specimens invisible to per-module counters — and the assertion-baked helper delivers exactly that cheaply.
**Flip criteria (shadow evidence required before ANY execution transfer):** ≥2 release cycles of family coverage on the priority targets (§A.5 ranks 1–6); zero false-dead flags on healthy mechanisms (a false `dead=true` on WatchReconcile falsifies the liveness signal); assertion results for every adopter; skip-reason distributions sane (no skip-storms).

### C.2 Phase 1 — pilot: OrphanWatcherSweepService (post-fix)

Weighed against the alternatives: **(a) the maintenance tier** — its predicate fix already landed (v0.31.11 [L]); as a *runtime* migrant it was always the worst first pick (never ran → no parity baseline: "engine runs it" vs "engine broke it" is unanswerable); as a pilot it now only carries the verification/convergence task, which needs no engine. **(c) a simple healthy sweep** (TmpImageCleanup / SourcesGc) — clean parity proof, lowest drama, but proves nothing about repairing broken lanes, and even the "healthy" candidates are partially dark (SourcesGc is INSUFFICIENT-EVIDENCE with its own PEXIT [C] — itself evidence for the observability contract). **(b) OrphanWatcher** wins: after the C.0 fix it is a *working, small, previously-trapped* module — first observability-family adopter on the exact motivating specimen (born-dead trap verified intact at live [L]), restoring coverage to a live aging failure class (the 2.39-day husk class). Under the gated recommendation no runtime migration occurs at pilot time (the conflation concern dissolves); if/when B is gated in, the *second* migrant should be a healthy sweep (parity proof: TmpImage, or JobLock which additionally gains a kill switch + healthy trace).

### C.3 Phase 2+ — ordering, gates, rollback

Ordering by §A.5 pain×simplicity: (1) JobLock + SourcesGc (PEXIT class — supervisor primitive is the win), (2) maintenance tier (full migration, with the landed job_metrics as its parity baseline), (3) bash reaper (real loop replacing piggyback), (4) RDRS lane consolidation, (5) strand-healer/drift, (6) the healthy tail, (7) telemetry/memoir last. **Per-phase house-standard gates:** unit pack registered in PACKS.md; mutation-verified regression pins for the state-machine transitions; promote ceremony (tag + 4-way sha + rollback_safe) with livez/soak gates; **rollback = one flag** (`SERVICES_<MODULE>_USE_ENGINE` OFF → legacy loop owns execution again; both paths compiled — dual-path design §B.2). Engine-wide OFF is the mass-rollback lever; engine failure posture is fail-to-legacy, never fail-stuck.

### C.4 Shadow-evidence accumulation → the B-gate (see §E.3)

The Phase-0/1 shadow data is precisely what feeds the escalation gate: contention windows, PEXIT deaths under `supervised()`, boot wall-clock SLO, event-substrate mass. If the gate fires, B is built with C's primitives as internals (§B.2 is the pre-approved design) and runs ITS OWN dormant phase before first execution transfer.

---

## D. The Honest Counter-Argument

### D.1 The case against any central engine (full strength)

1. **Added coupling:** today 55 mechanisms own their loops; one shared runtime makes every mechanism depend on one substrate — one bug degrades all of them. Today JobLockSweep can die while WatchReconcile stays perfect (independence is a real resilience property, census rows 1–2 [C]).
2. **A new single point of failure — who watches the watchman:** the daemon has **no supervisor tradition**. At least four loops die permanently on unexpected Exception with nothing re-spawning them (A3; JobLock verified intact at v0.31.22 [L]). "The engine will be supervised" is not an inherited property — it must be built and proven BEFORE 55 mechanisms depend on it. And the one catastrophic failure on record **was** a centralized scheduler starving silently for 6.54 days (v0.31.10, historical — fixed by the v0.31.11 composite [L]): the failure *shape* argument stands as a lesson even though the instance is repaired.
3. **Config sprawl:** the engine ADDS a registration surface on top of ~55 mechanisms' knobs (uneven kill switches GAP-16, dead knobs §8 [C]) unless it replaces them — and replacement means touching all 55.
4. **Migration risk vs actual pain:** the estate is mostly healthy — 21/33 observed executing in a 5.5h post-boot window, ≈**19/33 steady-state** (2 boot-adjacent maintenance rows carry the 6.54-day gap in their own evidence), 5/33 unverifiable (§E.5). The broken population is **small, named, and shrinking** — the Rev-2 re-base found two of the census's biggest pillars (maintenance starvation, GAP-02/03) **already closed by ordinary targeted fixes with no engine**, which is the strongest empirical form of this argument: point fixes are demonstrably closing these defect classes in this codebase.
5. **Centralizing what's fine decentralized:** the four topology shapes (census §10 [C]) exist for reasons — threads for starved-loop survival, per-entity loops for entity lifecycles, piggybacks for zero-overhead cadence. A single scheduler cannot host the thread class without re-introducing the starvation problem it exists to survive.
6. **Churn while L1 is in flight:** a 55-mechanism refactor touching api.py wiring + manager.py (4601–4792 [C]) risks conflicts and partial landings.

### D.2 The thinner alternative, steelmanned

Shared **primitives library** (single-flight, jitter, max-runtime, honest-state helper) + shared **observability convention** (the converged job_metrics family), adopted piecemeal; no central runtime, no registry, no scheduler process, no event bus. Buys: starvation-class fix for adopters (with a landed precedent — v0.31.11 did exactly this for the maintenance tier [L]); dormant-trap visibility (bus-is-None as a metric, not a DEBUG line); phase-lock breaking; per-module opt-in with no global cutover and no L1 conflict. Fails to buy: **event triggers** (a library cannot deliver "trigger on event X" without a substrate); **born-dead wiring traps** (the same code that forgot to wire the bus will adopt the library unwired — unless the assertion is baked into the helper, which §B.1 does); **wakeup consolidation** (not a scheduling problem at all); **enforcement** (a non-adopting module keeps lying — voluntary adoption is the daemon's documented failure mode: GAP-16's missing switches and §8's dead knobs are what voluntary discipline produced).

### D.3 Four-axis comparison

| Axis | Full engine (A) | Thin primitives (C) | Do nothing (D-content only) |
|---|---|---|---|
| Born-dead / starvation / counter-lies / PEXIT | YES **iff** it owns wiring+health+restart — all unbuilt today | PARTIAL→YES with §B.1: starvation/counter-lies already carry a landed precedent (v0.31.11 [L]); PEXIT + born-deed fixed by the supervised/assertion primitives baked into the helper | Point-fixes named ones; the class mechanism remains |
| Event triggers ("trigger on which event") | Engine-only territory (needs unified substrate, GAP-15) | **NO general triggers** — but the v1 catalog (§B.3) is deliverable as substrate repairs + EventBus/terminalization subscriptions without a runtime (terminalization now hookable today [L]) | NO |
| Wakeup consolidation (4.6%/3.3%) | NO (worker-pool claim path) | NO | NO |
| Config sprawl | Potential YES if it REPLACES knobs; risks ADDING a layer | PARTIAL (naming convention; removes nothing) | NO (dead knobs remain) |

### D.4 Does thin get ~80% of the win at ~20% of the risk?

On the **measured** pain — starvation invisibility, dormant-trapped mechanisms, PEXIT deaths, diagnostic cost — yes, and the Rev-2 re-base raised the share: two of the largest pillars are already closed by point fixes (the thin path's own method), so the *remaining* measured pain concentrates on exactly what the C layer targets (born-dead OrphanWatcher, bash reaper, PEXIT quartet, observability). On **event-trigger generality** — 0% (engine/substrate territory; v1 doesn't need generality). On **wakeup** — 0% for both options. The residual engine-only wins (boot staggering, one registration surface, enforced adoption) currently have **zero measured pain** behind them: no contention metrics exist for the 300s club, boot wall-clock has no SLO breach on record, and the enforcement argument is a values call (§E.4). That residual is exactly what the gate (§E.3) prices.

---

## E. Recommendation

### E.1 Verdict

**Gated hybrid — C+D now, B behind a measured gate, A rejected.**
- **Reject Option A** (full central engine, maximal framing): worst option on every lane's analysis (W2 matrix 2.30/5; both council models last place). It makes thread-death-as-signal mechanisms contract violations and buys its uniqueness with the highest-risk migration program on the table.
- **Do D's content immediately:** two live-defect fixes (OrphanWatcher late-bind, bash-reaper retention) + the maintenance verification/convergence task (C.0). Not hostage to any architecture program.
- **Build C now as Phase 0** (B.1): primitives library with the trap-impossibility guarantees baked in + observability **converged onto the landed `job_metrics` family**. Pilot adopter: the fixed OrphanWatcher (C.2). This is the honest version of "build the engine first but don't use yet": registry-as-observability delivers the Emperor's visibility goal with zero execution risk — and post-re-base, cheaper than Rev-1 assumed.
- **Hold B (subset engine, §B.2 design) behind the falsification gate** (E.3). The design is complete and pre-approved; nothing in C is thrown away (B uses C's primitives as internals); the gate converts "should we" from opinion into measurement.
- **Standalone forever:** ram-guard, wedge/loop-lag, readiness/livez, scheduler substrate, thread-death-as-signal heartbeats (A.6).
- **Event triggers v1:** fixed catalog (B.3) — terminalization already hookable at live via the landed S1 fire [L]; general registration API explicitly NOT v1.

### E.2 Why (the dominant axes)

**Risk and Maintainability dominate.** The evidence shows a *small named set* of structural failures inside a mostly-healthy estate (≈19/33 steady-state executing, §E.5), a *diagnostic-cost* problem (the census itself), and *zero measured pain* behind the full engine's unique advantages. The re-base strengthens this: the estate's worst recent failures were repaired by targeted patches, not architecture programs. Under those facts, the cheapest intervention that fixes every named defect class (C with baked-in guarantees) wins on risk; the engine's remaining uniqueness is real but unpriced — so price it with a gate instead of guessing. The council's sharpest framing: the observability family IS the registry in disguise; build the view first, earn the runtime second.

### E.3 The gate — two-sided falsification protocol

**Escalate C→B (build the subset engine) when ANY of:**
1. Post-jitter PG lock-wait/IO spikes at 300s-coincidence windows ≥2× off-window baseline (contention becomes real);
2. A live PEXIT death of a `supervised()`-wrapped loop (the wrapper thesis fails);
3. Boot wall-clock from the ~11 awaited serialized passes breaching an SLO the Emperor sets;
4. Event-substrate mass reaching the point where per-consumer polling is measurably wasteful.

**De-escalate B→C (if B was built) when ANY of:** tick drift >1 interval/24h; dispatch p95 >50ms; audit rows >0.5% of DB write volume or skip-rows nobody reads (observability without a consumer would not have caught the 6.54-day starvation — falsifies the premise); serialized queuing extending start-to-start >+10% of cadence; any in-scope member whose contract the engine cannot express (e.g. StalePause/RDRS thread topology).

**What Phase-0 shadow evidence would DISCONFIRM this recommendation:** (i) a false-dead/false-healthy flag on a healthy mechanism (falsifies the observability thesis — the family becomes noise); (ii) skip-reason storms from the honest-state helper (the cure is worse than the disease); (iii) adoption stalls below ~30% of priority targets in 2 cycles (voluntary adoption fails → coding-councilor's enforcement argument wins → build B now); (iv) the Emperor's actual pain turns out to be event-trigger generality, not machinery health (thin buys ~zero there → B or substrate program justified now); (v) live verification shows the v0.31.11 max_staleness escape hatch never fires under real load (the starvation class is NOT closed by point fixes → the structural-guarantee case strengthens).

### E.4 Pending decisions for the Emperor (answer → implication)

**Decision 1 — Runtime shape & timing (B now vs B gated).** *If gated (recommended):* C+D lands now; B is built only on a measured trigger (E.3) — fastest path to safe, nothing wasted if the gate never fires. *If B now:* the §B.2 subset registry is built immediately with the contract carried by registration (honest state, audit, kill switches, max-runtime) — justified if what you want is *uniformity of the runtime itself*, or if your confidence in process discipline (reviews enforcing voluntary adoption) is low; that is a values call the expert team split on exactly there (coding councilor: enforcement-by-registration, evidence-backed by GAP-16/dead-knob history; agentic councilor + governor + this synthesis: no measured pain behind B's uniqueness, and the estate's one catastrophic failure was itself a central scheduler).

**Decision 2 — Ratify the phased instinct (build-first-dormant → migrate one-by-one).** *If ratified (recommended):* Phase 0 = the C layer in shadow posture (observability + assertions, no execution transfer); Phase 1+ = one module at a time behind one-flag reverts; execution transfer only on flip criteria (C.1). *If not ratified (collapse phases):* fixes and adoption land as ordinary per-module patches with no shadow gate — faster by weeks, but you lose the falsifiable evidence trail that decides Decision 1 on data instead of opinion.

**Decision 3 — Event-trigger v1 scope.** *If fixed catalog (recommended):* ~4–6 trigger classes over substrates that exist (terminalization [hookable today, L], EventBus, state tables, attestation); GAP-14 depth triggers explicitly deferred until a consumer exists; no general registration API. *If defer all event work:* the two remaining substrate gaps (GAP-13 dead outbox, GAP-14 depth) persist; cheap plant points stay unplanted. *If fund substrate unification now:* GAP-15 envelope work + replay cursors become their own program — larger than this engine question, only justified if event-driven behavior (not machinery health) is the actual product pain.

### E.5 Confidence

**Medium** (both councilors self-assessed medium; convergence strong everywhere except the Decision-1 values fork). **Health arithmetic (Rev-2 restatement):** 21/33 liveness rows were observed executing **in a 5.5h post-boot window**; steady-state is ≈**19/33** (the 2 boot-adjacent maintenance rows — checkpoint-cleanup, vacuum — executed at 01:57–03:05 right after boot, and their own evidence carries the 6.54-day pre-boot gap); **5/33 unverifiable** through sampled channels; 2 dormant-trapped; 1 flag-off. Flip assumptions: (1) if the 5 unverifiable rows turn out mostly dead, the defect share grows from ≈8/33 to ≈13/33 — the enforcement case strengthens but does not flip the gate logic; (2) the remaining named defects (OrphanWatcher, bash reaper) are the actual pain — if the pain is event triggers, the ranking flips toward substrate work now; (3) L1 does not land a supervisor primitive (if it does, B's risk drops and the gate loosens); (4) live verification confirms the v0.31.11 maintenance fix fires under load (if not, starvation re-enters the repair queue and the structural-guarantee case strengthens).

---

## F. Appendix

### F.1 Input artifacts (one-paragraph summaries; full docs in this directory)

**`census-periodic.md`** — Code-truth census at `bf4bfde6e` of every daemon-side periodic/polling/sweep/recovery mechanism: 55 mechanisms across 13 families + 14 boot-only passes + dead knobs, with trigger/fail/kill/topology codes per row, a boot-order map (Phase 0 manager.initialize → Phase 1 sequential lifespan), the 300s phase-lock club, and three depth rows proving the defect classes: A1 OrphanWatcher deterministic wiring-order trap (born dead every boot, counters climb), A2 bash reaper (no loop, event-piggyback, silent no-op paths, boot-only retention), A3 JobLockSweep/SourcesGc permanent-exit loops with no supervisor and no healthy trace; §3 dissects the maintenance tier's permanently-due trap (idle gate + success-gated last_run ⇒ 500 skips / 0 executions invisible outside INFO logs). *(Rev-2 note: §3's trap anatomy is code truth at `bf4bfde6e`; the v0.31.11 composite fix post-dates this census.)*

**`census-events.md`** — Event-machinery census: 54 event rows (EV-01..EV-55) + 10 detail cards + 20 consolidated gaps. Confirmed at census time the two known defects (GAP-02/03 stranded-watcher class; GAP-04 global notification stream root-only). *(Rev-2 note: GAP-02/03 are CLOSED at v0.31.22 — S1 fire landed and wired [L]; GAP-04 verified still open [L].)* Headline structural findings: the designed turn-transitions outbox is DEAD (GAP-13 — payloads consumed only by DEBUG logs, cheapest future plant point; consumers still DEBUG-only at v0.31.22 [L]), queue-depth has zero plumbing (GAP-14), six incompatible fan-out primitives with different loss policies (GAP-15). Ends with the trigger-feasibility summary this study's §B.3 consumes.

**`liveness-evidence.md`** — Read-only live forensics against v0.31.22 (boot epoch 01:56:29Z, ~5.5h uptime window): 33 verdict rows — 21 EXECUTES (incl. 1 thin) / 3 BOOT-ONLY / 2 DORMANT-TRAPPED (OrphanWatcher ticking no-ops while its owned failure class ages live — husk 9c985fc7 at 2.39 days; bash reaper silent since 02:57:58Z, 3,604 spawns unreaped, 194k-row registry) / 1 DORMANT-by-flag (metrics census) / 5 INSUFFICIENT-EVIDENCE (≈8 maintenance jobs unverifiable through sampled channels). Headlines: maintenance 6.54-day starvation on v0.31.10 (43.2 GB cleared only by the promote boot — historical; fix landed v0.31.11 [L]); worker pools polling-dominated (4.6%/3.3% wakeup efficiency, 29,606 timeout-wakeups vs 613 notifications in 5.5h); metrics layer itself carries counter-vs-reality divergences. *(Steady-state reading of the 21/33 window count: §E.5.)*

### F.2 Process provenance (how this study was produced)

Architect-controller synthesis over four dispatched lanes, all read-only, all evidence-adjudicated (census-row/file:line citations verified present; no read-only violations):
- **W1 engine design** (worker `b5a7bc70`, skill `structural-design` — confirmed loaded): §B registration API, honest state machine, trap-impossibility, event surface, config continuity, migration skeleton, subset verdict.
- **W3 taxonomy** (worker `4a975d1a`, skill `data-flow-design`): §A families/overlaps/gaps/collisions/verdicts/standalone adjudications. Process note: W3 omitted the mandatory `Skill loaded:` first line; report structure and citation density are strongly skill-shaped, content adjudicated on evidence — recorded as a formatting miss, not a skill-bank miss (no re-dispatch warranted).
- **W2 counter-argument** (worker `8d849f8f`, skill `trade-off-analysis` — confirmed loaded): §D case-against, thin-alternative steelman, 4-axis comparison, weighted matrix (thin 3.80 / do-nothing 3.25 / full 2.30).
- **Council** (governor `d2a81a1a`, 2 councilors: models `agentic` + `coding`, skill `trade-off-analysis` each; both COMPLETED ≤16 min, both reports evidence-adjudicated, trap sites re-verified on disk by the coding councilor at `bf4bfde6e`): converged rejections/standalone/bug-fix-now/wakeup-out verdicts; split B-now vs B-gated; governor recommended gating; two-sided falsification protocol §E.3; pilot ruling §C.2.
- **Liveness lane** instance `73cc7df5` produced `liveness-evidence.md` (fence honored: GET-only probes, zero writes).
- **Rev-2 review re-base (2026-11-21):** reviewer verdict REJECT (bounded re-base) on Rev-1 — load-bearing citations re-verified against the **v0.31.22 tag**: maintenance starvation pillar re-based to HISTORICAL (v0.31.11 composite fix, maintenance.py:501/:210 + job_metrics `last_success_ts`/`consecutive_skips`); GAP-02/03 re-based to CLOSED (S1 `fire_for_terminal_child`, dependency_bus.py:1921, wired child_reports.py:6444/6643/6945 + error_reporting.py:693); anchors re-pinned (born-dead api.py:1920/:2972/:5710; PEXIT job_lock_sweep.py:241→:250; dead-outbox consumers instance_lifecycle.py:5976/:6589; root-only stream event_publisher.py:91; out-of-tree mint instance_ops.py:711/:596); health arithmetic restated to steady-state (§E.5); observability proposal converged onto job_metrics; Emperor decision subsection added (§E.4); house-fact provenance marked (commission-supplied: <1-core, off-loop v0.27.14; census: four topology shapes). Direction unchanged — reviewer's own assessment: recommendation direction stands, C+D gets cheaper, case-against strengthens.

*End of decision-report.md (Rev 2) — architect synthesis lane, 2026-11-21. Investigation-only: no code changed, no commits made.*
