# Phase 4 — Boot phase: BOOT_SWEEP / BOOT_PASS / FIRST_TICK classes, boot-SLO deadline, engine-independent watchdog, the single `api.py` wiring round

**Depends on:** P3 (guardrails, kill switches, dual-path).
**Feeds:** P7.
**Owns the ONLY `daemon/api.py` LIFESPAN edit in the program.** (P6 separately adds ONE `include_router` line to the router catalogue at `~:6359`; the two regions are ~2600 lines apart and neither touches the other's anchors. No other phase edits `api.py`.)

---

## 1. Objective

Make the engine *reachable from a boot* without arriving like the rest of the thundering herd, and answer the "who watches the watchman" objection **before** the estate is ever asked to depend on it:

1. **Boot-phase classes** — `BOOT_SWEEP` (awaited, **deadlined**), `BOOT_PASS` (awaited, best-effort, per-pass deadline), `FIRST_TICK_IMMEDIATE`, `FIRST_TICK_DELAYED(+offset)`, plus per-job first-tick **jitter** that desynchronizes the 300s club.
2. **The boot SLO is an acceptance gate, not a wish**: `boot_epoch` → readyz-green **≤ 120s** (Emperor-set, CLOSED). The engine's registration pass must be **cost-bounded** and must not sit on the readyz-green critical path.
3. **An engine-independent watchdog** — built AND drilled in this phase. It observes engine liveness from **outside** the engine's own task, and it is not the readiness composite (§A.6.3 keeps readiness standalone).
4. **ONE wiring round in `daemon/api.py`**: engine construction + registration pass + start, the boot wiring assertion call site, the watchdog start, and the shutdown stop — all in one contiguous, reviewer-legible block.

---

## 2. Scope

**In:** `daemon/services/engine/boot.py` (the four boot classes + deadline helper), `daemon/services/engine/watchdog.py`, the `daemon/api.py` wiring round, the boot-SLO acceptance check wiring, and the jitter/stagger arithmetic.

**Out:** the event registry (P5); the query/tab read surface (P6); any measurement instrumentation of its own beyond the engine's own timing (the *measurement method* and the *current-vs-bar numbers* belong to the sibling `boot-slo-measurement.md` — this phase **cites** the gate and wires the check).

---

## 3. Decisions

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D4.1 | Four boot classes, each a **field on `JobSpec.first_tick_kind` / a registration-time class**, not four code paths: `BOOT_SWEEP` (awaited before the lifespan yield, hard deadline) · `BOOT_PASS` (awaited, best-effort, per-pass deadline) · `FIRST_TICK_IMMEDIATE` (the tick fires at t=0) · `FIRST_TICK_DELAYED` (+ `first_tick_offset_s`) | §B.2:130 "Boot-phase classes: `BOOT_SWEEP` (awaited, deadlined), `BOOT_PASS` (awaited best-effort), `FIRST_TICK_IMMEDIATE`, `FIRST_TICK_DELAYED(+offset)`". Existing in-tree models: readyz refresher first tick t=0 (`daemon/api.py:5215-5217` docstring "the first tick fires immediately (t=0)"); drift +30s offset (`api.py:1808` task + census §5 "first tick +30s"); deferred DELETE_PHASE "(readyz-green AND +1s) OR (+5s hard)" (`api.py:1082-1093`); A6 awaited guaranteed boot pass (`api.py:3749-3819`) | a single "start it and hope" path (loses the deadlined class the SLO needs); deadline-free awaits (today's state: `daemon/services/boot_subprocess_sweep.py:85-88` = fail-open is the ONLY bound) |
| D4.2 | `BOOT_SWEEP`/`BOOT_PASS` deadlines are **per-pass config values** (`engine_boot_sweep_deadline_s`, default 15.0; `engine_boot_pass_deadline_s`, default 30.0) with `asyncio.wait_for`; an expired pass logs ERROR + continues (never aborts boot) | §B.2 + the boot-sweep posture ("boot hygiene must never block boot", `boot_subprocess_sweep.py:85-88`) **plus** the missing piece: today there is NO wall-clock bound anywhere in the boot path. The Emperor SLO supplies the number | a single global boot deadline (one slow pass would starve the rest); no deadline (the pre-existing condition the SLO exists to bound) |
| D4.3 | **Engine insertion point = the A6 region only**: the engine's construction + registration pass + start + watchdog start all land in ONE block **immediately before** the ServiceReconciliation A6 pass block (`daemon/api.py:3745-3749` comment block, pass at `:3809`), i.e. in the `~:3740` region. Shutdown lands in the existing sweep-stop cluster (`~:4560`, beside `service_reconciliation.stop()` at `:4600`) | Two reasons: (a) `init_dependency_bus` completes at `:2974`, so every dependency getter can resolve — the engine's registration is the LAST consumer of the boot wiring, so it belongs after everything else; (b) placing it after the readiness refresher task's creation (`:3117`) keeps it **off** the readyz-green critical path, which is the SLO-safe placement. **This also deliberately avoids the region the sibling `fix/phase1-live-defects` lane edits (`:1226-1272`, `:3018-3041`, `:4532-4547`)** | the `:996-1011` zone (research-anchors §2 suggests it as an alternative) — it sits BEFORE `setup_worker_pool()` and before every service construction, so nothing would resolve; also adjacent to the READYZ-green path. The `:2974-3041` zone — textually collides with the live-defects assertion hunk |
| D4.4 | **Per-job first-tick jitter** is deterministic-by-seed at registration (`jitter_seconds` → `hash(job_id) % jitter` offset, recomputed per boot), not random-per-tick | §B.2:130 "Per-job first-tick jitter desynchronizes the 300s club"; a per-tick random jitter would make the schedule unauditable and makes the "next_due" projection lie. Deterministic per boot = desynchronization without unpredictability | `random.uniform(0, jitter)` per tick (unauditable); a fixed global offset (re-creates the club) |
| D4.5 | **The 300s club's one deliberate breaker becomes first-class**: drift reconcile's `+30s` is expressed as `first_tick_offset_s=30.0`, not as a special case in a loop | §B.2:130 "drift's +30s becomes a first-class `first_tick_offset_s`". The *engine-side* representation is in scope; **actually migrating drift is not** (OUT-OF-SCOPE) — Phase 1 only proves the class exists and that `offset=30` behaves | special-casing drift in the engine (a hidden rule); migrating drift now (violates the one-by-one rule) |
| D4.6 | **Engine-independent watchdog**: a `threading.Thread(daemon=True)` that samples engine liveness (last tick monotonic timestamp + restart counter + `dead` job count) at a fixed cadence, **outside** the loop, and logs CRITICAL + emits a counted signal when the engine's last tick is older than `3 × tick_resolution + max_boot_window` | §A.6.2's rule ("1Hz TH, **NEVER a coroutine** … a loop-scheduled detector cannot detect loop starvation", `daemon/services/wedge_capture/watchdog.py:1-13`) + research-anchors §2 ("NOT the readyz composite itself — readiness must stay standalone per §A.6.3"). The thread-shape precedent is the wedge watchdog; the "sample a service's own health" precedent is `JobFeedbackObserver`'s health-check interval (`daemon/services/job_feedback_observer.py:384`) | a coroutine watchdog (cannot observe a starved loop — the exact blind spot); extending `/readyz`'s components (breaks `READINESS_COMPONENT_NAMES`, `daemon/services/readiness.py:95-100`, pinned at `tests/test_health_probes.py:157`) |
| D4.7 | The watchdog is **drilled** in this phase: an injected engine stall (a `sleep` longer than the threshold) must produce one CRITICAL line and one counter bump, and must NOT be able to kill the engine | §B.2 + the D.1.2 objection: "The engine will be supervised" is not an inherited property — it must be **built and proven BEFORE** 55 mechanisms depend on it. The drill is the proof | "the watchdog exists, trust it" (the unverified-supervisor posture the study rejects) |
| D4.8 | Watchdog cadence is a config value (`engine_watchdog_interval_s`, default 5.0), **never** a literal, and its stall threshold derives from `tick_resolution_seconds` at start | Fence-adjacent house rule from §B.5: nothing hardcodes a cadence. Verified drift warning: ram-guard's poll default is **1000ms** at this tree (`daemon/config.py` `RamGuardConfig`, `guard.py:180`), not the 200ms the census saw — exactly the L1-lane drift the rule protects against | a literal 1s/5s in the thread |
| D4.9 | The engine's **registration pass is cost-bounded** and logs its own elapsed ms; the acceptance check asserts the whole `:3740-:3820` block adds **≤ 250ms** to boot in the dormant posture (registration of 0-2 fixture jobs, no execution) | The SLO (≤120s) is the outer bar; a cost bound on the NEW block keeps the engine from consuming the bar it must not regress. Measured against the boot_epoch→readyz-green clock. | "it's only a few ms" (unmeasured — the exact class of assumption the SLO forbids) |
| D4.10 | `daemon/api.py` gets **one** contiguous engine block + **one** shutdown block, each guarded by `try/except Exception` that logs and continues (never aborts boot), matching the house wiring posture | House posture precedent: the ServiceReconciliation block's own comment "failure logs and CONTINUES (never aborts boot)" (`api.py:3749-3756`, `:3812-3819`); every sweep wiring block in the lifespan is `try/except` wrapped (`:1910-1938` etc.) | un-guarded wiring (one engine bug takes the daemon down at boot — strictly worse than no engine) |
| D4.11 | Engine **dormancy is preserved at the wiring level**: the boot block registers the engine, starts the tick task, and starts the watchdog, then logs exactly ONE INFO line naming the posture (`enabled=… shadow=… registrations=N`). With N=0 the block is a no-op beyond that line | Emperor phase rule ("build the engine first but don't use yet"); §B.2's `SHADOW`/`ENABLED` axes. This is the difference between "built" and "used" | registering the 12 continuity jobs at boot (that is Phase 2 of the migration program — explicitly OUT) |

---

## 4. Task decomposition

### 4.1 The explicit awaited/timed boot-pass inventory (explorer open item (b) — resolved)

The commission requires an **explicit** list, not the census's "~11". Measured at THIS tree: **29 awaited/await-shaped calls on the lifespan path before the yield** (`daemon/api.py`, yield at `:4056`), plus 7 `create_task` fire-and-forget tasks and 1 thread cluster.

| # | Line | Call | Class |
|---|---|---|---|
| 1 | 926 | `await auto_test_rag()` | awaited (may `SystemExit`) |
| 2 | 958 | `await manager.initialize()` | awaited (heaviest: create_all + migrations + PG ensure + maintenance boot sweep) |
| 3 | 1003 | `await manager._execution_gate.recover_stale_leases()` | awaited (deadlined by its own design) |
| 4 | 1033 | `await _boot_rehydrate_question_packs(manager)` | awaited |
| 5 | 1139 | `await job_lock_manager.recover_stale_job_locks()` | awaited, try/except |
| — | 1199 | `await reconcile_stale_bash_spawns(...)` | **NOT a lifespan await** — inside `_bash_reconcile_background()` spawned by `create_task` at `:1214` (live-defects lane adds a sibling) |
| 6 | 1481 | `await manager._baseline_service.record_initial_baselines_for_all_predefined_agents(...)` | awaited, conditional (auto-reset) |
| 7 | 1487 | `await manager._baseline_service.detect_drift_for_all_predefined_agents(...)` | awaited, same block |
| 8 | 1673 | `await retry_scheduler.start()` | awaited, enabled-gated (default DISABLED) |
| 9 | 1720 | `await job_recovery.recover_on_startup()` | awaited |
| 10 | 1734 | `await asyncio.to_thread(...mark_orphaned_running_interrupted)` | awaited to_thread (snapshot boot sweep) |
| 11 | 2931 | `await job_queue_service.reconcile_terminal_watches()` | awaited |
| 12 | 2966 | `await job_feedback_observer.start()` | awaited |
| 13 | 2974 | `await init_dependency_bus(app, manager)` | awaited (registers the bus singleton at `:5712`) |
| 14 | 2982 | `await job_queue_mgmt_service.auto_provision_system_queues(system_project_id)` | awaited |
| 15 | 2990 | `await asyncio.to_thread(...)` (seed backfill) | awaited to_thread |
| 16 | 3012 | `await asyncio.to_thread(...)` (jobs backfill) | awaited to_thread |
| 17 | 3039 | `await job_processor.start()` | awaited |
| 18 | 3086 | `await asyncio.to_thread(manager._project_repository.list_projects)` | awaited to_thread |
| 19 | 3088 | `await job_queue_mgmt_service.auto_provision_system_queues(project.project_id)` | awaited, loop |
| 20 | 3094 | `await manager.start_sources()` | awaited |
| 21 | 3382 | `await start_auth_background(app.state, config.auth, manager.engine)` | awaited |
| 22 | 3540 | `await upgrade_journal_sweep.sweep_wake_records()` | awaited |
| 23 | 3590 | `await continue_running_instances_after_restart(...)` | awaited (AutoContinue) |
| 24 | 3595 | `await sweep_orphaned_live_turns(...)` | awaited |
| 25 | 3651 | `await sweep_stale_pauses(...)` | awaited, enabled-gated |
| 26 | 3802 | `await service_reconciliation.start()` | awaited |
| 27 | 3809 | `await service_reconciliation.sweep_once()` | awaited **A6 guaranteed boot pass** |
| 28 | 3851 | `await vscode_manager.attach_existing()` | awaited, try/except |
| 29 | 3937 | `await _auto_start_vscode_if_preferred(manager, vscode_manager)` | awaited |

Fire-and-forget (`create_task`, not awaited): delete_doomed `:1084`, bash reconcile `:1214`, drift reconciler `:1808`, waiting-children `:2490`, long_tool_nudge `:2556`, readiness refresher `:3117`, memoir workers `:3168`/`:3275`/`:3341`.

**Consequence for this phase:** the engine's block is inserted **after #27 (A6, `:3809`)** — i.e. at position 28 of the awaited sequence — so it neither delays readyz-green (the refresher task at `:3117` has already been ticking through the awaits at #21-#27) nor touches any of the 29 passes' anchors. **The SLO bucket this phase must not regress is therefore the `boot_epoch` → (first readyz composite write) window**, not the full lifespan body.

### 4.2 Tasks

| ID | Task | Outcome | Files / anchors |
|---|---|---|---|
| **T4.1** | `boot.py` — `BootClass` enum, `run_boot_pass(coro, deadline_s)` (deadline via `wait_for`, expire → ERROR + continue), `run_boot_sweep(...)`; first-tick scheduling (`immediate` / `delayed`+offset / jitter offset) fed into the tick loop's initial due-times | each class has a unit test; a pass that overruns its deadline is abandoned (no boot abort) and logged; a jittered job's first due-time is stable across ticks within one boot and differs between two jobs | `daemon/services/engine/boot.py`; models `daemon/api.py:1082-1093` (readyz-green-gated deferral), `:5215-5217` (first tick t=0), `:1808` (+30s offset), `:3809` (A6 awaited pass) |
| **T4.2** | `watchdog.py` — the thread, the sample shape (`last_tick_monotonic`, `tick_count`, `restart_count`, `dead_jobs`, `running_jobs`), the stall predicate, CRITICAL log + counter, `start()`/`stop()` | thread runs off-loop; a stalled engine (last tick older than threshold) produces exactly one CRITICAL per episode; the thread can never raise into the daemon | `daemon/services/engine/watchdog.py`; shape `daemon/services/wedge_capture/watchdog.py:1-13` ("NEVER A COROUTINE"); health-interval precedent `daemon/services/job_feedback_observer.py:384` |
| **T4.3** | The **stall drill** | a test that freezes the engine's tick (injected long `await`) ⇒ watchdog fires once, counter increments, engine survives (the drill is the acceptance evidence) | `tests/unit/services/engine/test_watchdog_drill.py` |
| **T4.4** | **The ONE `api.py` wiring round** — a single contiguous block at the A6 region: construct `EngineService` (config + repos + guardrails + killswitch), run the deadlined registration pass, `start()` the tick task, `start()` the watchdog, store both on `app.state`, log the posture line | the block is `try/except`-guarded (logs + continues); with zero registrations it is a no-op beyond one INFO; boot proceeds identically when it fails | `daemon/api.py` — insert before the ServiceReconciliation comment block at `:3745-3749` (pass at `:3809`) |
| **T4.5** | `api.py` shutdown block | engine stops **before** the sweep cluster (so no engine tick can touch a service the sweeps are stopping), watchdog stops, both `app.state` handles cleared | `daemon/api.py` — inside the sweep-stop cluster, before `:4600` (`service_reconciliation.stop()`), after the tmp-image/sources blocks |
| **T4.6** | Boot wiring assertion **call site** (the function is P3's T3.1) | called immediately after the registration pass; an unresolved dependency logs ERROR naming `job_id → dependency` + a summary line, and boot continues | `daemon/api.py` (inside the same block) |
| **T4.7** | The **boot-SLO acceptance check**: assert the engine block's elapsed (registration pass + start) is measured and logged, and that the whole-block cost is ≤ the bound; assert `boot_epoch` and the first readyz-composite write are both observable so a measurement lane can compute `readyz_green − boot_epoch` | the check is wired to the **measurement method** (below), not to a self-report | pack `test/packs/engine_boot_slo_gate.sh` (P7 registers it); method §4.3 |
| **T4.8** | Author the P4 unit pack (boot classes, jitter determinism, watchdog + drill, wiring-posture line, shutdown order) — registers in P7 | pack present | `test/packs/engine_boot_unit_test.sh`, `tests/unit/services/engine/test_boot.py`, `test_wiring.py` |

### 4.3 The boot-SLO measurement method this phase wires (the sibling owns the numbers)

**Gate (Emperor-set, CLOSED):** `boot_epoch` → readyz-green **≤ 120s**.

**Both anchors exist in code today** (verified at this tree, and confirmed by the boot-telemetry explorer result in shared context):

| Anchor | Where | How it is read |
|---|---|---|
| start | `capture_boot_epoch(manager.engine)` called at `daemon/api.py:969-971`; the value is logged by `daemon/services/boot_epoch.py:132` — `"Boot epoch captured (DB clock): <iso>"` (DB clock, naive-UTC) | the log line (the ONLY persisted-ish retrieval path; there is no DB column, no metric) |
| end | first write of `app_state.readiness_composite` in `_periodic_readiness_refresh_loop` (`daemon/api.py:5309`, loop defined `:5190`, task created `:3117`, first tick t=0); `/readyz` then returns 200 (`daemon/api.py:6598`) | the composite's first non-`None` timestamp, or a `/readyz` 200 poll against a booting daemon |

**Known traps the measurement must respect** (from the explorer result): launcher `.launcher-state` updates only on exit; "uptime at livez OK" can lag for hand-back/probe reasons and is not boot cost; a graceful DRAIN under DB outage adds ~20 min that is not boot time; host wall-clock must never be mixed into the DB-naive-UTC comparison frame. **The measurement method itself (current numbers vs the bar) is `boot-slo-measurement.md` — a sibling worker's deliverable.** This phase cites the gate and wires a check; it does not duplicate the measurement.

**The must-not-regress acceptance this phase owns:**

| Check | Assertion |
|---|---|
| SLO-1 | The engine block's own elapsed (registration pass + start + watchdog start) is measured with `time.perf_counter` and logged in the posture line; in the dormant posture (0-2 fixture registrations) it is **≤ 250ms** |
| SLO-2 | The engine block adds **no** awaited pass in front of the readiness refresher's task creation at `:3117` — enforced structurally by the insertion point (D4.3) and pinned by a byte-offset test (the `tests/test_settings_source_order_pin.py` discipline: assert the engine block's anchor string appears **after** `readiness_task = asyncio.create_task(`) |
| SLO-3 | A rehearsal run (dev daemon, dormant engine) computes `first readyz composite → boot_epoch` and records it in the gate output; the **dev** rehearsal is the evidence, the **live** number is the sibling's |

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| Boot classes + deadlines + first-tick scheduling (T4.1) | 6 | 10 | component 7 of the brief (10/8-14) |
| Watchdog + drill (T4.2, T4.3) | 5 | 8 | new thread + predicate + a real stall test |
| The `api.py` wiring round + shutdown (T4.4, T4.5, T4.6) | 5 | 8 | one contiguous block, guarded, plus the ordering pin |
| Boot-SLO gate wiring (T4.7) | 2 | 3 | measurement method cited, check authored |
| P4 unit pack authored (T4.8) | 2 | 4 | |
| **Phase total** | **20** | **33** | ~**26 mid** |

---

## 6. Fences that constrain this phase (this is the phase that touches the most pins)

**F-5 — `daemon/api.py` byte-offset ordering pin (HARD).**
`tests/test_settings_source_order_pin.py` pins `"\n    config = load_config()"` → `"apply_db_settings_overrides("` → `"InstanceManager("` in that order, with all four anchors required to resolve (`find() != -1`). **Constraint:** the engine block must not move, duplicate or shadow any of those strings; it must not introduce a second `\n    config = load_config()` occurrence. Insertion point D4.3 (`~:3740`, well after `InstanceManager(`) satisfies this by construction; the pack asserts it.

**F-8/F-12 — readiness must not learn the engine (HARD).**
`daemon/services/readiness.py:95-100` defines `READINESS_COMPONENT_NAMES = ("database","queue_freshness","services","checkpoint_saver")`; `tests/test_health_probes.py:157` pins `body["components"]` exactly; `tests/unit/tools/test_readyz_grace_detail.py:56-70` pins `constants.READINESS_GRACE_HORIZON_S == 120.0` and the tracker wiring. **Constraint:** the engine contributes ZERO readiness components; the watchdog is a separate thread with its own log surface. The 120 is *also* the SLO number — two different 120s that must not be conflated in code (the grace horizon is a readiness-hold window; the SLO is a boot wall clock).

**F-13 — boot-sweep pins (HARD, both directions).**
`tests/test_opt_o8_stale_subprocess_sweep.py` pins the stale-subprocess sweep's default-ON (`:464-500` `test_default_is_on_pinned`) and env-override (`:502-527` `test_env_override_wins_in_dev_test`) behavior; `tests/helpers/ram_guard_fence.py:8, :30` and `tests/packs/ram_guard_phase6_mutation_matrix.py:27, :299` pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** (`env -u` strips the ambient guard — the 2026-10-31 incident where a frozen-binary boot sweep SIGTERM/SIGKILLed live processes). **Constraint:** the engine's `BOOT_SWEEP` class is a NEW, engine-internal concept and must NOT be wired into `daemon/services/boot_subprocess_sweep.py`, must not read or write `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`, and must not add a pre-`load_config()` boot hook (that module runs at `daemon/__main__.py:730-740`, **before** config load — the engine's deadline knobs live in `EngineConfig`, which does not exist yet at that point). Name collision risk is real and is resolved by naming: the engine class is `BootSweepClass.BOOT_SWEEP` inside `daemon/services/engine/boot.py`, never a second `sweep_stale_subprocesses`.

**F-14 — the deferred DELETE_PHASE precedent must not be perturbed.**
`daemon/api.py:1082-1093` fires a background task on "(readyz-green AND +1s) OR (+5s hard floor)". **Constraint:** the engine block must not read, set, cancel or re-time that task, and must not itself depend on readyz-green (a dependency on readiness would couple the engine's start to the composite — the coupling §A.6.3 forbids for probes, and a boot deadlock risk in the other direction).

**F-15 — `tests/integration/test_vscode_routing.py:433-435` pins the SPA catch-all as the LAST route.** No new *route* in P4 (the engine adds no route at all here). P6's read surface must be mounted before the catch-all — recorded there.

**F-16 — the sibling lane's `api.py` hunks (`fix/phase1-live-defects`).**
Measured regions: `:1226-1272` (bash-retention wiring), `:3018-3041` (post-`init_dependency_bus` boot assertion), `:4532-4547` (bash-retention shutdown). **Constraint:** D4.3's insertion at `~:3740` and the shutdown block before `:4600` are textually disjoint from all three. Recorded so the reviewer can check the claim rather than trust it.

---

## 7. Dependencies

- **Phase:** P3 complete (guardrails' assertion function, killswitch resolver, `EngineService` supervisor).
- **External:** the sibling `fix/phase1-live-defects` branch (merge-order facts in phase3 §6.1 — no code dependency).
- **Blocks:** P7 (the packs assert this phase's wiring/pins), P8 (the staged release carries it).

## 8. Parallelism

- P4 is **serial after P3** and is the program's second serialization point (it owns the only `api.py` edit).
- P5/P6 may run **concurrently with P4** (disjoint: `daemon/services/event_registry/`, `daemon/services/engine/projection.py` + routers).
- Worktree-lane parallelizable: yes, but **only one lane may touch `daemon/api.py`** — a hard rule for this program.

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The engine block regresses boot | the Emperor's 120s bar is breached by the thing meant to improve visibility | D4.3 placement (after A6, off the readyz-green path) + SLO-1's ≤250ms bound + SLO-2's structural ordering pin + SLO-3's rehearsal number |
| A boot pass hangs forever | boot never reaches readyz | every `BOOT_SWEEP`/`BOOT_PASS` has a `wait_for` deadline; expiry logs and continues (D4.2) |
| The watchdog itself starves or dies | "who watches the watchman" returns one level up | the watchdog is a thread (survives loop starvation by construction) with its own `try/except` per tick; its death is visible as a missing heartbeat on the `/metrics`-family surfaces P6 exposes (family registration is P6's ceremony) |
| The watchdog's CRITICAL is ignored (the L4 lesson: journal-only CRIT unseen ~4h) | a real engine death goes unnoticed | the watchdog ALSO increments an in-process counter exposed by the engine's snapshot (P6 reads it), and the drill asserts both. Log-only alerting is explicitly not the plan (the install-protection blueprint's L4 lesson) |
| Wiring lands in the wrong `api.py` region and collides with the live-defects lane | merge pain, duplicated boot assertions | D4.3 + F-16; the reviewer can diff regions |
| `app.state` handle leaks across a lifespan restart | a stopped engine is restarted twice, or a stale watchdog observes a dead tick | shutdown clears both handles; `stop()` is idempotent (P2 T2.5) |
| The drill is vacuous (a test that cannot fail) | a false sense of supervision | the drill's mutation is "remove the stall predicate" ⇒ no CRITICAL ⇒ RED (recorded in the pack) |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | `BOOT_SWEEP`/`BOOT_PASS` **deadline**: a pass that sleeps past `deadline_s` is abandoned, logs ERROR naming the pass, and boot continues (asserted by a lifespan-level test with a stub pass). Mutation: `wait_for` removed ⇒ RED |
| AC2 | First-tick classes: `IMMEDIATE` fires at t=0; `DELAYED(offset=30)` first fires at ~30s; jitter offsets differ across ≥5 jobs and are identical on re-evaluation within one boot. Mutation: jitter removed (all offsets equal) ⇒ RED |
| AC3 | **Watchdog drill**: an injected engine stall ⇒ exactly ONE CRITICAL per episode + the counter incremented; the engine task survives and resumes. Mutation: the stall predicate removed ⇒ RED |
| AC4 | **Wiring**: with zero registrations, boot logs exactly ONE posture INFO (`enabled=… shadow=… registrations=0`) and the lifespan's remaining passes are unaffected; with the wiring forced to raise, boot continues and logs a WARNING. Mutation: the `try/except` removed ⇒ RED |
| AC5 | **Shutdown order**: engine stops before `service_reconciliation.stop()`; both `app.state` handles are `None` after; a second `stop()` is a no-op |
| AC6 | **Boot SLO**: SLO-1 (block ≤250ms dormant), SLO-2 (block anchor appears after `readiness_task = asyncio.create_task(`), SLO-3 (a rehearsal computes `first_readyz_composite − boot_epoch` and records it — the live number belongs to `boot-slo-measurement.md`) |
| AC7 | **Fences**: `tests/test_opt_o8_stale_subprocess_sweep.py` unchanged-green (F-13); `tests/test_health_probes.py` + `tests/unit/tools/test_readyz_grace_detail.py` + `tests/unit/services/test_boot_epoch.py` unchanged-green (F-8/F-12); `tests/test_settings_source_order_pin.py` green (F-5); `tests/integration/test_vscode_routing.py` catch-all pin green (F-15) |
| AC8 | **Zero estate contact**: with the shipped defaults, the engine registers nothing, executes nothing, and boot's awaited-pass count before the yield is unchanged at 29 (the engine block is a start, not an awaited pass on the estate's behalf) |
