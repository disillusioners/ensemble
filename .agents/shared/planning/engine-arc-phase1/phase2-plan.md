# Phase 2 — Execution core: tick loop, execution classes, single-flight, honest state machine, durable run record

**Depends on:** P1 (models, `EngineConfig`, `JobSpec`/`register()`).
**Feeds:** P3 (guardrails wrap this), P5 (Event triggers dispatch through this), P6 (this phase's projection is read there), P7.

---

## 1. Objective

Make the engine an **honest scheduler that runs nothing by default**: one lifespan asyncio task owns a tick loop over the registered jobs; each due job is dispatched through its declared concurrency class; every dispatch is single-flighted, bounded by `max_runtime_s`, retried per policy, and recorded — terminal transition or not — into `engine_jobs` (mutable) + `engine_runs` (append-only), with a RAM cache for hot reads.

**The load-bearing claim of this phase is not speed.** The engine adds scheduling overhead only (house `<1 core` constraint, commission-supplied). It is **not** an efficiency play and must never be described as one: what it buys is a *uniform, auditable, trap-proof execution contract*, not throughput. Any performance claim in code comments, docs or commit messages is out of bounds.

---

## 2. Scope

**In:** `daemon/services/engine/execution.py` (tick loop, dispatch), `state.py` (the state machine + stamping), `locks.py` (single-flight), `dispatch.py` (the three execution classes + timeout/retry), `metrics.py` (the in-process projection), plus `daemon/services/engine/engine.py` (`EngineService` construction/start/stop — **not** its lifespan wiring, which is P4).

**Out:** dependency getters / dead detection / kill switches / fail-to-legacy / dual-path harness (P3); boot classes / jitter staggering of the boot herd / api.py wiring / engine-independent watchdog (P4); event-kind catalog (P5); query/read surface (P6); **any** migration of a real module (the whole program's OUT-OF-SCOPE).

---

## 3. Decisions

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D2.1 | **One** engine task owns the tick loop: `while not stopping: compute due-set → dispatch → await sleep(resolution)`. Resolution is a config field (`tick_resolution_seconds`, default 1.0), **never** a per-job sleep loop | §B.2:120 "Single engine task owns the tick loop". A per-job loop would reproduce the 55 independent loops the engine replaces | per-job `while True: await job(); await sleep(interval)` (re-introduces N loops) |
| D2.2 | Dispatch by `concurrency_class`: `MAIN_LOOP` = awaited inline on the engine task (pure in-memory jobs only, asserted by a doc contract + a P7 pin that no `MAIN_LOOP`-registered job's `execute_fn` is a DB caller); `DB_OFFLOAD` = `await asyncio.to_thread(fn)`; `DEDICATED_EXECUTOR` = a small dedicated `ThreadPoolExecutor(max_workers=tick_workers, default 2)` via `loop.run_in_executor` | Off-loop discipline v0.27.14 (commission-supplied); estate idiom `await asyncio.to_thread(...)` (research-anchors §4); `ThreadPoolExecutor` precedents: `daemon/services/db_offload.py:342` (6 workers), `daemon/services/plane_sync_service.py:254` (2), `daemon/mcp/warmup_pool.py:116` | routing DB work through `MAIN_LOOP` (blocks the graph loop — the exact class the estate already paid for); using the shared `db_offload` executor for CPU-bound work (steals the estate's DB permits) |
| D2.3 | **Single-flight** per job: `asyncio.Lock` keyed by `job_id`, acquired **before** dispatch on the engine loop, held across the executor hop, released in `finally`. A due job whose lock is held → `SKIPPED_OVERLAP` **counted, never queued** | §B.2:122 verbatim. Precedent: `MaintenanceRunLock` (`daemon/services/maintenance_run_lock.py`, 126 lines — single-flight via one DB-claim row). Locks live in a dict on the engine; the DB claim row is the cross-process half (D2.4) | queueing the overlap (unbounded backlog, and "overlap" becomes invisible); DB-only single-flight (a DB round-trip per tick per job — the hot-read path must be RAM) |
| D2.4 | Two-level single-flight: RAM `asyncio.Lock` (intra-process, the fast path) **plus** the `engine_runs` insert as the durable claim record (`boot_id` + `host_pid`). Cross-process exclusivity is NOT attempted in v1 | Phase 1 is dormant and single-daemon by assumption (the estate's standing assumption); a DB before/after stamp is the audit, not a distributed lock. `MaintenanceRunLock`'s DB-claim shape is the model if a second process ever appears | a distributed lock now (unneeded, and the fail-closed polarity question would have to be answered without a consumer) |
| D2.5 | **Honest state machine** as an explicit transition table in `state.py`: `IDLE → DUE → RUNNING → {SUCCEEDED, SKIPPED_<reason>, FAILED_BACKOFF, TIMED_OUT}`. **Every terminal transition stamps** `last_run_at`, `last_outcome`, `last_skip_reason`, and appends exactly one `engine_runs` row | §B.2:124. The landed v0.31.11 composite is the in-production precedent for exactly this contract in the maintenance tier: stamp-on-attempt `daemon/services/maintenance.py:524-528`, stamp-on-success `:530-532`, `_record_skip` `:568-601` | success-gated stamping (the permanently-due trap: `maintenance.py:260-269` @ `bf4bfde6e` is the historical shape, since fixed) |
| D2.6 | Skip-reason vocabulary is **closed**: `IDLE_GATED`, `KILL_SWITCHED`, `DEP_MISSING`, `SHADOW`, `OVERLAP`, `TIMEOUT_PENDING_EXTEND`, `ERROR_BACKOFF`, `NOT_DUE`, `SKIPPED_REREGISTERED` [+SKIPPED_REREGISTERED @P3, sanctioned AMD-10; none dropped]. A free-text reason is folded to a declared member (the metrics model's `allowed_values` fold precedent, `daemon/checkpoint_metrics.py:770-790`) | §B.1's enum + §B.2's `SKIPPED_<reason>`; a free-text reason makes `skips_total{reason}` unbounded cardinality | free-text reasons (unbounded labels — the one thing `allowed_values` exists to prevent) |
| D2.7 | Timeout: `asyncio.wait_for` around the dispatch with `max_runtime_s`. `timeout_policy=cancel` → cancel the await and record `TIMED_OUT`; `timeout_policy=extend` → record `TIMEOUT_PENDING_EXTEND` and **do not release the lock** — the job keeps its single-flight claim until it actually finishes (the next tick sees `OVERLAP`) | §B.2:112 `timeout_policy: cancel|extend`. `extend` must not create a second concurrent execution (that would break single-flight); holding the lock is the only shape that honors both | `extend` = "ignore the timeout and start anyway" (overlap); cancel-only (refuses the LLM-bound class the memoir workers are) |
| D2.8 | Retry: `retry{max, backoff_base_s, backoff_cap_s}` with exponential backoff `min(cap, base × 2^(attempt-1))` and **no jitter** (jitter belongs to scheduling, not to retry — a jittered retry makes the consecutive-skip/backoff arithmetic unauditable). After `max` attempts the outcome is `FAILED_BACKOFF` and the job returns to `IDLE` (due again at the next interval), never a permanent death | §B.2:112 retry triple; the estate's `Task` retry uses base/max/multiplier (`daemon/config.py` `task_retry_backoff_base` / `_max`) — same shape, expressed in the spec | retry with jitter (unauditable backoff); unbounded retry (a wedged job never yields the tick) |
| D2.9 | **Backoff on consecutive idle-skips**: after `N` consecutive `IDLE_GATED`/`DEP_MISSING` skips the job's effective due interval doubles up to `interval × skip_backoff_cap_factor` (default 8), reset on any RUN. Every skip still stamps | §B.2:124 "Backoff on consecutive idle-skips prevents skip-storms". Without it, a gated job with a 1s resolution writes a stream of skip rows | no backoff (skip-storm audit table); hard-suppressing skips (the very invisibility this program exists to end) |
| D2.10 | **Priority 0-9** orders the due-set within one tick (0 = highest). Stable sort, ties broken by `job_id` | §B.2:112 `priority 0-9`. Deterministic ordering makes a tick reproducible in tests | arrival-order (non-deterministic, untestable) |
| D2.11 | **Due-ness** computed from the RAM cache: `due ⇔ now − last_run_at >= effective_interval` (and, for `Event` triggers, a pending-event latch). `last_run_at` is stamped on EVERY terminal transition, **including** skips, so due-state can never strand | §B.2:126 trap-impossibility (a); the RAM cache is seeded from `engine_jobs` at engine start (D2.12) | due-ness from the DB per tick (a DB round-trip per job per tick on the hot path); stamping only on success (the permanent-due class) |
| D2.12 | **DB is source of truth, RAM is the hot read**: at `start()` the engine hydrates the cache from `list_jobs()`; every terminal transition writes `engine_jobs` (off-loop via `to_thread`) and updates the cache **after** the write lands (cache follows DB, never leads it) | §B.2:124 "DB source-of-truth … RAM cache for hot reads — RAM-only is how OrphanWatcher's lie survived restarts". Restart-clock precedent: `maintenance.py:624-687` `hydrate_persisted_state` (persisted last-success is the source of truth; a restart does not reset the clock) | RAM-only (the exact defect class this program exists to fix); DB-per-read (hot path) |
| D2.13 | **`SHADOW` posture**: when `ENSEMBLE_ENGINE_SHADOW_MODE` is on, the tick computes due-ness and appends an `engine_runs` row with `outcome=SKIPPED_SHADOW` **and never invokes `execute_fn`** | Emperor phase rule + §B.2 `SHADOW_MODE`. This is the posture Phase 1 ships in, and the only posture in which the engine touches the estate at all | "shadow = run but discard the result" (that is execution; the ruling says do not use yet) |
| D2.14 | Projection (`metrics.py`) is **in-process state, not a metrics family yet**: `snapshot()` returns per-job `{next_due, last_duration_seconds, last_outcome, skips_by_reason, consecutive_skips, dead, running, would_fire}` | Constrained by fence **F-2**: `job_metrics()`'s block shape is pinned at `tests/unit/test_maintenance_idle_starvation.py:800-804` (`set(block) == {"last_success_ts","consecutive_skips"}`) — the engine **cannot** add fields there. Constrained by fence **F-1**: the 28-verdict / 18-registered metrics census is closed (`tests/unit/metrics/test_metrics_family_adjudication.py:415-453, :883-906`) — a new family is a conscious census ceremony, not a side effect. So P2 publishes an engine-local snapshot; P6 owns the convergence/mapping + any family registration | adding fields to `job_metrics()` (breaks a live pin); registering `ensemble_engine_*` metrics here (breaks the census pin and hijacks a separate lane's ceremony) |
| D2.15 | `dead=true` is **computed**, not stored, in P2 (zero runs in `2 × effective_interval`); P3 owns the persisted `dead` column write + boot assertion escalation. **Observer carve-out (CS-9 — leader ruling, approver iteration-002, verbatim in `decisions.md` §Leader rulings): for specs with `execute_fn` withheld, dead ⇔ zero `SKIPPED_SHADOW` rows in `2 × effective_interval` (the observable execution-substitute) — the zero-runs definition alone would hold every observer spec dead=true forever** | Split so that P2 has no dependency on the guardrail module. The DB column exists from P1; P2's projection reports the computed fact | writing `dead` in P2 (needs the boot-assertion context P3 owns) |
| D2.16 | The engine **never hardcodes any estate cadence**. `ram_guard`'s poll interval, the drift `+30s` offset, and every `SERVICES_*` default are read from config at registration/tick and never inlined | §B.5: "nothing in this design may hardcode it (read config at registration/tick; ram-guard stays standalone regardless)". Verified at this tree: `RamGuardConfig.poll_interval_ms` `daemon/config.py:1232`-block (default **1000**, `guard.py:180`), NOT the 200ms the census saw — the drift is exactly why the rule exists | any literal cadence (the L1 lane may move it under us) |

---

## 4. Task decomposition

| ID | Task | Outcome | Files / anchors |
|---|---|---|---|
| **T2.1** | `state.py` — the transition table + `stamp_terminal()` helper (one write path for both `engine_jobs` and `engine_runs`) | the table is data (a dict of allowed transitions); every terminal path funnels through `stamp_terminal`; a skip cannot bypass the stamp | `daemon/services/engine/state.py`; precedent `daemon/services/maintenance.py:524-532, :568-601` |
| **T2.2** | `locks.py` — `JobLockTable` (dict of `job_id → asyncio.Lock`), acquire/release with `finally`, `try_acquire_nowait()` | overlapping dispatch returns `False` without awaiting; release is idempotent; a lock leak is impossible by construction (`finally`) | `daemon/services/engine/locks.py`; model `daemon/services/maintenance_run_lock.py` |
| **T2.3** | `dispatch.py` — the three execution classes + `run_with_timeout` + retry loop | each class dispatchable in isolation (unit-testable without the tick loop); timeout honored per policy; retry arithmetic matches D2.8 | `daemon/services/engine/dispatch.py`; anchors `daemon/services/db_offload.py:386` (`offload`), `:342` (executor), `daemon/services/plane_sync_service.py:254` |
| **T2.4** | `execution.py` — the tick loop: hydrate → due-set (priority sort) → dispatch per job → sleep(resolution); a per-job exception never kills the tick | one tick executes in isolation; a raising `execute_fn` is caught and converted to `FAILED_BACKOFF`; the loop's own unexpected error logs and re-raises to a **supervised** restart boundary (P3 owns the supervisor) | `daemon/services/engine/execution.py`; loop shape `daemon/services/watch_reconcile_sweep.py:364-375` (sleep-between-ticks), `daemon/services/job_lock_sweep.py:234-250` (Template-B catch→return — the shape the supervisor must convert) |
| **T2.5** | `engine.py` — `EngineService` (construct/start/stop; hydrate; task name `engine-tick`; cancel+await with a bounded stop budget) | `start()` idempotent; `stop()` cancels and awaits within 5s (the `OrphanWatcherSweepService` stop budget precedent, `orphan_watcher_sweep.py:177-198`); no lifespan wiring yet | `daemon/services/engine/engine.py`; anchors `orphan_watcher_sweep.py:153-198`, `sources_gc_service.py:210, :250-252` |
| **T2.6** | `metrics.py` — the in-process `snapshot()` per D2.14 | snapshot keys are exactly the declared set; `skips_by_reason` keys ⊆ the D2.6 vocabulary; `dead` computed per D2.15 | `daemon/services/engine/metrics.py` |
| **T2.7** | Shadow posture (D2.13) threaded through the tick | with `shadow_mode` on, zero `execute_fn` invocations are observable (a counting spy in the pack) and one `SKIPPED_SHADOW` run row per due job per interval exists | `execution.py` |
| **T2.8** | Hydration + restart-clock (D2.12) | a restart does not reset `last_run_at` (the cache seeds from the row); a persisted `consecutive_skips` survives | `engine.py`; precedent `maintenance.py:624-687` |
| **T2.9** | The **convergence mapping** (P6 consumes it): one function `project_merged(maintenance_service, engine_service) -> dict` that yields, per job id, the union of `job_metrics()`'s two fields and the engine's snapshot — **without** editing `job_metrics()` | the mapping is pure (no writes, no state); `job_metrics()`'s shape is untouched (F-2 green) | `daemon/services/engine/metrics.py` (function only; P6 wires it) |
| **T2.10** | Author the P2 unit pack (state-machine transitions, single-flight under concurrency, timeout both policies, retry arithmetic, shadow zero-execution, hydration) — registers in P7 | pack present | `test/packs/engine_execution_unit_test.sh`, `tests/unit/services/engine/test_state.py`, `test_dispatch.py`, `test_execution.py` |

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| Execution model + `EngineService` (T2.3-T2.5) | 9 | 13 | three classes + timeout + retry + start/stop; component 2 (20/14-28) minus the boot/wiring halves P4 owns |
| Single-flight (T2.2) | 3 | 4 | the RAM lock is ~60 lines; `MaintenanceRunLock` (126 lines) is the DB-claim model |
| State machine + persistence + shadow + hydration (T2.1, T2.7, T2.8) | 7 | 10 | component 4's state/stamp half; the projection half is T2.6/T2.9 |
| Tick loop + due-set + priority (T2.4) | 3 | 4 | |
| Projection + convergence mapping (T2.6, T2.9) | 3 | 3 | |
| P2 unit pack authored (T2.10) | 3 | 4 | 3 files |
| **Phase total** | **28** | **38** | ~**33 mid** |

---

## 6. Fences that constrain the design

**F-2 — `job_metrics()` is byte-shape-pinned (binds D2.14 hard).**
`tests/unit/test_maintenance_idle_starvation.py:800-804` asserts, for a registered maintenance job, `set(block) == {"last_success_ts", "consecutive_skips"}`. Adding `next_due` (or anything) to that dict turns a live pin RED. **Therefore:** the engine's projection is a *separate* snapshot, and the "converge, don't mint" requirement (§B.1) is satisfied by the **mapping function** (T2.9) that unions both sources at read time. The engine does not edit the maintenance tier's contract to publish its own numbers. Routed follow-up (not this phase): if the estate later wants one dict, that is a deliberate pin-lift in the maintenance pack.

**F-1 — the metrics family census is closed (binds D2.14 hard).**
`tests/unit/metrics/test_metrics_family_adjudication.py:415-434` pins exactly 28 verdicts (12 IN + 6 PARTIAL, and `REGISTERED_FAMILIES == full + partial`); `:440-452` requires every registered family to carry a band; `:883-906` bounds the total against `GLOBAL_SERIES_CAP = 2000`. **Therefore:** P2 registers **no** `ensemble_engine_*` metric. Registration is phase6 T6.3's explicit ceremony (verdict row + `REGISTERED_FAMILIES` member + `FAMILY_SERIES_BANDS` entry + allowlist record if the module sits outside `WIRING_ROOT` (`tests/unit/metrics/test_metrics_family_adjudication.py:391`)). A P2 that quietly calls `register_family_metric` would RED the pack **and** widen a closed census without a decision.

**F-3 — `_NAME_RE` dormancy fence (binds the column names used by the stamp path).**
`tests/test_resource_control_seam.py:294-346`: no `daemon/**/*.py` file except `daemon/models/resource_monitor.py` may name `action_by` / `acted_at`; no string literal may combine `resource_pid_map` with `action`. **Therefore:** the stamp path uses `last_outcome` / `stamp_terminal` / `last_skip_reason` only. A "who acted" audit column is out of bounds without a conscious fence edit.

**F-7 — Template-B permanent-exit loops are the shape this phase must NOT reproduce.** `daemon/services/job_lock_sweep.py:241-250` (catch → log → `return`) is verified intact at HEAD; the same shape exists in `sources_gc_service.py` and `tmp_image_cleanup_service.py` per census. **Therefore:** `EngineService`'s own loop must never convert an unexpected exception into a silent `return`. P2 exposes the error to a supervisor boundary (P3) and logs at ERROR; a `return` here would recreate the exact "who watches the watchman" defect the program is fixing.

**F-8 — readyz must not learn about the engine.** `daemon/services/readiness.py:95-100` pins `READINESS_COMPONENT_NAMES == (database, queue_freshness, services, checkpoint_saver)` and `tests/test_health_probes.py:157` pins the components dict. **Therefore:** the engine adds NO readiness component; its liveness is the engine-independent watchdog's business (P4), and readiness stays a standalone probe (§A.6.3).

---

## 7. Dependencies

- **Phase:** P1 complete (models, config, spec).
- **External:** none.
- **Consumes:** `daemon/services/db_offload.py` (`offload`, executor config); the `asyncio.to_thread` idiom; `MaintenanceRunLock` as the single-flight model; `maintenance.py`'s stamp/skip shape as the honest-state model.
- **Blocks:** P3 (wraps `execution.py`), P4 (needs `EngineService.start/stop`), P5 (needs the event dispatch seam), P6 (needs the projection).

## 8. Parallelism

- **Serial within P2** — the five modules share the state model.
- **Parallel across phases:** P2 and P5 run concurrently after P1 (disjoint trees). P2 must publish the Event-trigger dispatch hook **without importing P5** (D2.11's pending-event latch is fed by a `ingest_event(kind, payload)` call that P5 invokes).
- Worktree-lane parallelizable: yes; the P5 lane branches from the P1 tip.

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The tick loop becomes a new single point of failure | every registered job stalls at once — the "who watches the watchman" objection (§D.1.2) | P4's engine-independent watchdog is a **hard** part of the program (not optional), and P2's loop must never `return` silently (F-7). Phase 1 ships `enabled=true` + `shadow_mode=true` with N observer specs registered, zero executables (`execute_fn` withheld), `USE_ENGINE` all unset — so the blast radius at ship time is nil BECAUSE nothing executes (dispatch is structurally impossible). |
| `extend` timeout policy leaks the single-flight claim forever | a job silently never runs again | `extend` stamps `TIMEOUT_PENDING_EXTEND` every tick it is still running (visible in `skips_by_reason` and `dead`), and the P3 guardrail escalates a `2 × max_runtime_s` hold to `dead=true` |
| RAM cache leads the DB after a failed write | the projection claims a run that the audit does not have | D2.12 ordering: cache updates **after** the write lands; a failed write leaves the cache stale and logs ERROR (the tick continues) |
| Skip rows grow unbounded on a gated job | audit table bloat | D2.9 backoff (interval × up to 8) + the `(job_id, started_at)` index; a 30d prune is the routed follow-up (P1 §5 note) |
| `MAIN_LOOP` class is used for DB work by a future registrant | the graph loop blocks — the exact estate defect | spec doc contract + P7 pin: `MAIN_LOOP`-class `execute_fn` must not reference a repository/engine symbol (AST check, house style like `tests/job_queue/test_terminal_write_census.py`) |
| The projection is read as a metrics family | a future lane assumes `/metrics` carries `ensemble_engine_*` | D2.14 states it is in-process only; P6 owns the family ceremony; phase2's docstring says so explicitly |
| Shadow mode invoked `execute_fn` by accident | the Emperor's "don't use yet" rule is violated | T2.7's pack leg counts invocations (must be 0 under shadow) — a mutation (drop the guard) REDs |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | **State machine:** each of the 8 transition classes is exercised; every terminal transition writes exactly one `engine_runs` row AND stamps `last_run_at`/`last_outcome`/`last_skip_reason` on `engine_jobs`. Mutation: removing the stamp from the skip path ⇒ RED (the permanently-due class) |
| AC2 | **Single-flight:** two concurrent ticks on the same job produce exactly ONE `RUNNING` and ONE `SKIPPED_OVERLAP`; no second execution begins while the lock is held. Mutation: releasing the lock before the executor hop ⇒ RED |
| AC3 | **Timeout:** `cancel` policy yields `TIMED_OUT` within `max_runtime_s + resolution`; `extend` policy yields `TIMEOUT_PENDING_EXTEND` and the job remains non-due while running. Mutation: `wait_for` removed ⇒ RED |
| AC4 | **Retry:** attempts follow `min(cap, base × 2^(n-1))`; exhaustion lands `FAILED_BACKOFF`; no jitter is applied (deterministic timing assertion) |
| AC5 | **Shadow:** with `ENSEMBLE_ENGINE_SHADOW_MODE` on, the invocation counter for `execute_fn` is **0** across ≥3 intervals, and `skips_by_reason["SHADOW"]` == the number of due evaluations. Mutation: the shadow guard removed ⇒ RED |
| AC6 | **Off-loop discipline:** a `DB_OFFLOAD`-class job's `execute_fn` is observed on a **non-loop thread** (thread-ident assertion); a `DEDICATED_EXECUTOR` job on a pooled thread with the declared max; a `MAIN_LOOP` job on the loop thread |
| AC7 | **Restart-clock:** hydrate a job with a persisted `last_run_at = now − interval/2`; after start, the job is NOT due; with `now − 2×interval`, it IS. Mutation: hydration removed ⇒ RED |
| AC8 | **Fence greens:** `tests/unit/test_maintenance_idle_starvation.py` `test_job_metrics_exposes_one_block_per_job` still passes unchanged (F-2); `tests/test_resource_control_seam.py` passes unchanged (F-3); `tests/unit/metrics/test_metrics_family_adjudication.py` passes with **zero** delta (F-1 — proof that P2 minted no family) |
| AC9 | **Dormant:** with the Phase-1 shipped defaults (all `SERVICES_*_USE_ENGINE` unset) and the boot block's **N observer specs** registered (zero `execute_fn`, per AMD-1/D4.11), a boot runs the loop, logs one INFO line, and **shadow rows are BY DESIGN one `SKIPPED_SHADOW` row per due job per interval** (D2.13; AMD-1 — not "no rows": the observatory's evidence is the shadow record itself). `daemon/api.py` is unmodified by this phase |
