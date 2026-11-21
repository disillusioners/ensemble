# Engine Arc Phase-1 — Research Anchors (explorer-1)

**Tree:** `/home/nea/ensemble-src-wt-engine-arc-p1` — branch `feature/engine-arc-phase1`, HEAD `aed3eabce` = base `b71742fe3` (v0.31.23) + planning docs. **All anchors re-grepped at this tree 2026-11-21.** Census anchors were taken @ `bf4bfde6e`; drift corrections in §9.

---

## 1. JOB-QUEUE SUBSTRATE

### Models — `daemon/repositories/job_queue/models.py`
- `AdmissionState(str, Enum)` :21-48 — `QUEUED="queued"` / `ACTIVE="active"` / `DONE="done"` / `DEAD="dead"` (:40-43). Sole write authority under queue-proxy model (docstring :22-39; legacy `JobStatus` removed Phase 7b).
- `ACTIVE_ADMISSION_STATES` frozenset :56-58 + `active_admission_states_sql()` :61-69 — EVERY membership filter (ORM `.in_()` AND raw-SQL `IN (...)`) MUST route through this constant (:51-55). Rule an engine_jobs implementation must respect if it touches job_queue_items.
- Legacy `JobStatus` test shim :126-139 — production code MUST NOT import it (:117-121).
- `Decision` enum :148-169 — NO_RETRY / RETRY / DEAD_LETTER; required at the single terminal-write boundary `_finalize_terminal` (:162-165).
- `JobQueue` table :190-253 (`job_queues`) — CheckConstraints `ck_job_queues_queue_type` :194, `ck_job_queues_defer_concurrency` :195; `Index("idx_job_queues_project", "project_id")` :196; `UniqueConstraint("project_id","queue_name_lower", name="uq_job_queues_project_name")` :197; PK `queue_id` :201-204; `concurrency_limit: int = Field(default=1, ge=1, le=20)` :213; validator enforces defer/background ⇒ concurrency_limit=1 :223-237.
- **`JobItem` table :256-462 (`job_queue_items`)** — pure queue ticket (:262-270: execution lifecycle lives on `Instance`, read via resolver). Fields:
  - PK `job_id` (uuid4 default) :317-320
  - `agent_id` :323, `agent_dir` :324, `agent_tag` :329-332, `message` :333, `source` :334 (default "api")
  - `project_id` :337, `queue_id` FK → `job_queues.queue_id` :338
  - `priority: int = Field(default=5, ge=1, le=10)` :341
  - `admission_state` :346-354 — default QUEUED **plus `sa_column_kwargs={"server_default": text("'queued'")}`** (:348-353 keeps `create_all` in sync with migration DEFAULT; raw-SQL INSERTs omitting the column otherwise violate NOT NULL)
  - `created_at` :357 (ISO str), `instance_id` :360, `job_metadata` JSONBType :363-366, `deleted_at` :369 (soft delete)
  - `job_type` :372 ("task" serial | "message" parallel)
  - `retry_count` :375, `max_retries` :376, `idempotency_key` :377 (max_length=255), `next_retry_at` :378, `failed_at` :379-383 (retry marker), `terminal_reason` :402 (index=True; values completed/failed/cancelled/aborted/orphan_retired :384-401)
  - `version: int` :416 optimistic lock + `__mapper_args__ = {"version_id_col": ...}` :421 (defense-in-depth on every UPDATE/DELETE)
  - Indexes (`__table_args__` :273-314): `idx_job_queue_instance` :274, `idx_job_queue_project` :275, `idx_job_queue_items_queue` :276, `idx_job_queue_admission_state` :281, **partial-unique `idx_job_idempotency` :300-306** with dual-dialect `sqlite_where`/`postgresql_where` (`idempotency_key IS NOT NULL AND deleted_at IS NULL`), `idx_job_queue_terminal_reason` :313.
- `JobLock` table :478+; `JobLockInfo` in-memory model :465-475.

### State machine — `daemon/services/job_state_machine.py`
- `VALID_TRANSITIONS` :53-62 — exactly 8 entries:
  - queued→active (start), queued→done (cancel pending), active→done (complete/fail/cancel/abort NO_RETRY), active→queued (retry RETRY), active→dead (dead-letter), done→queued (replay from done), dead→queued (replay from DLQ), done→active (orphan-race post-commit re-arm)
- Same-state = implicit no-op, NOT enumerated (:13-21, `can_transition` :87-110). Creation = INSERT, never a transition (:22-27). `InvalidTransitionError(ValueError)` :65-81.

### Migrations — `daemon/migrations/versions/`
- **134 .sql files** at this tree (blueprint's "119" is stale; the earlier "127" here was a stale study-note count — architect FA-2 census, tree-verified at approver iteration-001). Naming convention: `YYYYMMDD_HHMMSS_<snake_name>.sql`.
- **3 NEWEST:** `20261119_000001_add_proposal_decision_columns.sql`, `20261116_083000_add_room_resolution_warnings.sql`, `20261115_120000_add_room_proposal_dedup_columns.sql`. Current max number = `20261119_000001` → next free slot is any `20261121_HHMMSS_*` (date-prefix rules; FA-2 pins `20261121_120000/120001` free).
- **Style model (pick):** `20261018_090003_create_resource_pid_map.sql` (93 lines) —
  - Header comment block with What-lives-here + **DUAL-DRIVER NOTES** :24-34 (SQLite: this file runs via the runner every startup; PostgreSQL: schema comes from `SQLModel.metadata.create_all` off the model + late-import block in `daemon/manager.py`; PG-side runner is a NO-OP) + **RUNTIME-SPLITTER NOTE** :40-43 (no inline `;` inside comments — runner strips only full-line `--`).
  - UP: `CREATE TABLE IF NOT EXISTS` :47-68; named `CREATE [UNIQUE] INDEX IF NOT EXISTS` :74-85 incl. partial-unique-with-WHERE :74-76 (byte-identical to model `__table_args__` — 3-site lockstep :35-38).
  - DOWN: reverse-order DROP INDEX/TABLE :87-93.
- **Mandatory reading:** `20261109_021154_create_memoir_v2_tables.sql` :64-73 — WHY every CREATE carries IF NOT EXISTS (boot order: `create_all` runs FIRST, the migration file is applied IMMEDIATELY AFTER; a bare CREATE raises "already exists", the runner swallows it on its idempotent-skip branch, and the file degrades into a silent no-op on every boot). Also :42-49 EVERY STRING COLUMN IS TEXT (never VARCHAR, either driver); :50-58 3-site index lockstep (model ↔ migration ↔ test pack) with DESC-ordered indexes declared after the class; :75-80 plain `ADD COLUMN` (SQLite has no `ADD COLUMN IF NOT EXISTS` — hard syntax error).
- House rule honored: `engine_jobs.sql` and `engine_runs.sql` = two SEPARATE files, each with model in its own module registered via the manager.py late-import block before `create_all` (app_settings / resource_monitor / board / bash_spawn / memoir precedent).

---

## 2. BOOT-PHASE HOOK POINTS

### Pre-lifespan (`daemon/__main__.py`, `main()`)
- `_boot_auth_config_preflight()` :700 (static misconfig enumeration, exit 78, BEFORE config load).
- **BOOT_SWEEP (stale-subprocess sweep): call site :730-740** — `sweep_stale_subprocesses()` :733 inside a fail-open belt (:736-740); runs BEFORE `load_config()` :743, uvicorn, and any child-spawning (:722-727 rationale).
- Module `daemon/services/boot_subprocess_sweep.py`: kill-switch `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` :120 — lane-conditional default (:67-97): prod lane (`sys.frozen`, `is_prod_lane()` :140+) = ON, dev/test/manual = OFF; explicit value wins BIDIRECTIONALLY in ALL lanes; OFF = complete no-op (:79-80). Grace knob `ENSEMBLE_BOOT_SWEEP_TERM_GRACE_SECONDS` :121 (default 5.0, clamp [0.5, 30.0] :128-130). **No wall-clock deadline exists** — fail-open is the only bound (:85-88); deliberately NOT an app_settings key (:63-65: runs before config load).

### Lifespan (`daemon/api.py:690 async def lifespan(app)`) — ordered awaited passes (re-verified)
1. `await manager.initialize()` :958 — engine creation, create_all + migrations, maintenance boot sweep inside (W1 retry; see :1115-1119 comment → `daemon/services/maintenance_boot_sweep.py`).
2. **BOOT_EPOCH STAMP: `capture_boot_epoch(manager.engine)` :969-971** — after engine exists, before any worker heartbeat. Service `daemon/services/boot_epoch.py`: `capture_boot_epoch` :91-127 (DB-time `SELECT now()`/`CURRENT_TIMESTAMP`, first-capture-wins, best-effort), `get_boot_epoch` :70-76, `set_boot_epoch` :79-88, `BOOT_EPOCH_FLOOR = datetime.min` :64; module docstring :1-46 (stop-frozen heartbeat amnesty; consumers: readiness queue_freshness + StaleTaskRecovery). Secondary capture inside `manager.discard_on_startup` manager.py:1488-1498.
3. Message-queue boot recovery :980-984 (`recover_stuck_messages_on_startup`, sync, never wedges boot).
4. `probe_critical_notes_boot_state` :992-994.
5. `await manager._execution_gate.recover_stale_leases()` :1003 (awaited so first `gate.run` sees clean state — precedent for an awaited deadlined pass).
6. `manager.setup_worker_pool()` :1011 (sync).
7. `await _boot_rehydrate_question_packs(manager)` :1033 (single ordered entry point after wiring; placement rationale :1014-1032).
8. MigrationWorker :1039-1040; MaintenanceApiService :1049-1060 (both sync constructions).
9. Deferred DELETE_PHASE background task :1082-1093 — fires on (readyz-green AND +1s) OR (+5s hard floor) per D-P3.9 (:1062-1081). **This is the existing boot-phase/deferral precedent for DELAYED+offset.**
10. Job repo + lock repo + `JobLockManager` :1123-1127; `await job_lock_manager.recover_stale_job_locks()` :1138-1149 (awaited; try/except never crashes startup).
11. Drift reconcile: `drift_interval = config.services.drift_reconcile_interval_seconds` :1756 → `_periodic_drift_reconcile_loop(` invoked :1809 (def :4758; first tick +30s offset per census).
12. Snapshot boot sweep `await asyncio.to_thread(...mark_orphaned_running_interrupted)` :1730-1736.
13. `await retry_scheduler.start()` :1673 (constructed :1662-1672; `poll_interval=60.0` :1669; enabled gate `config.job_system.job_retry_scheduler_enabled` :1664, default None=DISABLED config.py:3315).
14. `recovery_stats = await job_recovery.recover_on_startup()` :1720 (JobRecoveryService :1691-1719).
15. **OrphanWatcherSweepService constructed :1910-1931, `orphan_watcher_sweep.start()` :1936 (NOT awaited)** — born-dead trap INTACT: bus dependency captured at construction (`self._dependency_bus = dependency_bus`, orphan_watcher_sweep.py:131-138) BEFORE `await init_dependency_bus(app, manager)` :2974 and `set_dependency_bus(bus)` :5712. When None the sweep still ticks and DEBUG-logs only (orphan_watcher_sweep.py:101-108). This is the live instance of the `resolve_at_first_tick()` target defect.
16. JobLockSweepService wiring :1963-1985; WatchReconcile :2019-2034; `MissionStrandHealer(` :2067 (interval read :2058); TmpImageCleanup :2112-2120; `SourcesGcService(` :2143-2145; PlaneSyncWatchdog :2397-2413; `WaitingChildrenWatchdog(` :2467-2471.
17. DLQ + retry-engine wiring :2943-2955.
18. `await job_feedback_observer.start()` :2966 (constructed :2958-2965 with `manager._event_bus` :2960).
19. `await init_dependency_bus(app, manager)` :2974 — def :5347; `bus.start()` :5373; crash recovery `_recover_fired_unsent` :5374+.
20. System-default-project bootstrap :2976-3027 (awaited to_thread backfills :2990, :3012).
21. `await job_processor.start()` :3039 (constructed :3030-3038, `poll_interval=30.0` :3035).
22. `await manager.start_sources()` :3094.
23. Upgrade-journal wake sweep `await upgrade_journal_sweep.sweep_wake_records()` :3540.
24. Stale-pause boot-zone pass :3640-3656 (enabled gate :3644; `sweep_stale_pauses` :3649).
25. ServiceReconciliation :3764-3819 — `await service_reconciliation.start()` :3802 + **A6 BLOCKING awaited boot pass `await service_reconciliation.sweep_once()` :3809** ("runs BEFORE the lifespan yield — the guaranteed boot pass", :3749-3754; failure logs and CONTINUES :3807, :3817-3819). **This is the canonical awaited-guaranteed-boot-pass template.**
26. Readyz refresher: def :5197 (asyncio loop; **first tick t=0** :5215-5217; components: database SELECT 1 with hard 500ms budget :5204-5206, queue_freshness :5207-5212, services job_processor+live_hub :5213, auth advisory :5237-5239). Handler serves the cached composite O(1) :3111.
- **`/readyz` route:** `@app.get("/readyz", response_model=ReadyzResponse)` :6598 (also late registration :6164; route catalogue note :6111). `/livez` :6578 zone. **readyz flips green** when the refreshed composite is all-green — the engine-boot gate must not perturb components (§8 fences).
- Shutdown: `await manager.shutdown()` :4620; `shutdown_dependency_bus` :5725; `set_dependency_bus(None)` :5722/:5741; OrphanWatcher stop :4458-4467.

### Natural insertion points (for the plan)
- **Engine registration sweep (awaited, deadlined):** after `capture_boot_epoch` / around :996-1011 zone (before `setup_worker_pool`) — same slot as execution-gate lease recovery; or immediately before ServiceReconciliation's A6 pass (established "awaited guaranteed boot pass" template :3749-3819).
- **BOOT_SWEEP-deadlined classes:** engine-owned passes should adopt the sweep's fail-open + explicit-env-override semantics (boot_subprocess_sweep.py:63-105) but ADD a deadline (none exists there today — Emperor SLO boot_epoch→readyz-green ≤120s is the binding wall clock, boot_epoch stamped :971, readyz-green observable via the refresher loop first tick t=0 :5215).
- **FIRST_TICK_IMMEDIATE / DELAYED+offset:** existing models — readyz refresher first tick t=0 (:5215-5217); drift reconcile +30s offset (:1809/:4758 zone); deferred DELETE_PHASE (+1s green-gated / +5s hard) :1082-1093.
- **Engine-independent watchdog:** model = readyz refresher loop (5197, simple asyncio loop, fail-soft per tick :5219-5223) + JobFeedbackObserver health-check pattern (`self._health_check_interval = config.observer_health_check_interval_seconds`, job_feedback_observer.py:384). NOT the readyz composite itself (readiness must stay standalone per decision-report §A.6.3). **[SUPERSEDED SHAPE — review fold F-3 / AMD-2, 2026-11-21:** the asyncio-loop model wording above is the RESEARCH-TIME shape, retained as research history. The **BUILT** watchdog is a daemon THREAD — `threading.Thread(daemon=True)`, precedent `daemon/services/wedge_capture/watchdog.py:1-13` ("NEVER a coroutine": a loop-scheduled detector cannot detect loop starvation, the exact engine-tick wedge the watchdog exists to catch). Drill documentation must match what is built (D4.6, phase4-plan.md:37); the loop-starvation rationale is why the shape changed.**]**

---

## 3. SERVICES_* KNOBS

### Mechanics
- `class ServicesConfig(BaseSettings)` config.py:2143; **`model_config = SettingsConfigDict(env_prefix="SERVICES_")` :2146** — every field auto-maps to `SERVICES_<FIELD_NAME_UPPER>` env.
- config.yaml interpolation: services block processed at config.py:9142-9168 (`services_config` dict → `config_dict["services"]`; `service_tool` sub-dict special-cased :9150-9167; `service_tool_reconcile_interval_seconds` hoisted :9162-9167). Other prefixes: OPENAI_ :142, DAEMON_ :590, LIMITS_ :645, RESOURCE_MONITOR_ :1011, RAM_GUARD_ :1232, PERSISTENCE_ :1300, QUEUE_ :1394, AGENTS_ :1582, COMPACTION_ :1612, SLASH_COMMANDS_ :2048, JOB_SYSTEM_/NOTIFY_USER_/etc. per class.

### The 12 in-scope knobs — field def (config.py) → read site
| # | Knob | Field def | Read site | Service class |
|---|------|-----------|-----------|---------------|
| 1 | watch_reconcile | `watch_reconcile_sweep_interval_seconds` :2906 (env docstring :2922) | api.py:2023 | WatchReconcileSweepService watch_reconcile_sweep.py:91 |
| 2 | mission_strand_heal | `mission_strand_heal_interval_seconds` :2938 | api.py:2058 | MissionStrandHealer mission_strand_healer.py:236 |
| 3 | report_delivery_recovery | `report_delivery_recovery_enabled` :2258 + `_interval_seconds` :2267 (+8 lane switches :2297-2424) | wired in worker pool per census (pool_orchestrator.py:363-538 @ census; NOT re-grepped here — see Open questions); kill-switch mention routers/recovery.py:71 | ReportDeliveryRecoveryService report_delivery_recovery.py |
| 4 | sources_gc | `sources_gc_enabled` :3033 + `sources_gc_interval_seconds` :3054 | api.py:2143 / :2145 | SourcesGcService sources_gc_service.py:96 |
| 5 | eligible_pending | `eligible_pending_sweep_interval_seconds` :2664 | api.py:1846 | EligiblePendingSweepService eligible_pending_sweep.py:72 |
| 6 | orphan_watcher | `orphan_watcher_sweep_interval_seconds` :2695 + `_grace_seconds` :2716 | api.py:1916 / :1919 | OrphanWatcherSweepService orphan_watcher_sweep.py:85 |
| 7 | job_lock | `job_lock_sweep_interval_seconds` :2771 | api.py:1967 | JobLockSweepService job_lock_sweep.py:93 |
| 8 | tmp_image | `tmp_image_cleanup_interval_seconds` :3141 (store max bytes :3112-3123; retention :3178; `SERVICES_TMP_IMAGE_CLEANUP_ENABLED` DELETED per amendment :3134) | api.py:2112 | TmpImageCleanupService tmp_image_cleanup_service.py:89 |
| 9 | plane_sync_watchdog | `plane_sync_watchdog_interval_seconds` :2832 (backoff base :2856, max :2866, max attempts :2879) | api.py:2397 | PlaneSyncWatchdogService plane_sync_watchdog_service.py:86 |
| 10 | stale_pause | `stale_pause_sweep_enabled` :2426 + `_interval_seconds` :2434 (age bound :2443, batch cap :2462) | api.py:3644 / :3654-3656 (boot-zone pass) | daemon/services/stale_pause_sweep.py |
| 11 | waiting_children_watchdog | `waiting_children_watchdog_enabled` :2609 + `_interval_seconds` :2619 (hang threshold :2647) | api.py:2471 | WaitingChildrenWatchdog waiting_children_watchdog.py:569 |
| 12 | drift_reconcile | `drift_reconcile_interval_seconds` :2246 | api.py:1756 → loop :1809/:4758 | JobRecoveryService `_periodic_drift_reconcile_loop` |

### Dead / misfiled knobs (current line numbers, consumers verified)
- **`lease_heartbeat_interval_seconds` — config.py:3220** (ServicesConfig ⇒ `SERVICES_LEASE_HEARTBEAT_INTERVAL_SECONDS`). ZERO consumers: only hit in daemon/ is the definition; `execution_gate.py:97 DEFAULT_LEASE_HEARTBEAT_SECONDS = 30.0` is an independent class constant that shadows it. **DEAD confirmed.**
- **`worker_poll_interval` — config.py:2148-2151** (ServicesConfig ⇒ `SERVICES_WORKER_POLL_INTERVAL`, default 0.5). ZERO consumers (grep across daemon/: definition only). **DEAD confirmed.**
- **`checkpoint_interval` — config.py:1309** (`PersistenceConfig` :1297, env_prefix `PERSISTENCE_` :1300 ⇒ `PERSISTENCE_CHECKPOINT_INTERVAL`, default 1). ZERO consumers; sibling module constant `daemon/constants.py:169 CHECKPOINT_INTERVAL: int = 1` also has no read site. **DEAD confirmed.**
- **Misfiled #1: `observer_health_check_interval_seconds` — config.py:3313**, lives in **JobSystemConfig** (:3298), NOT ServicesConfig. Has a live consumer: job_feedback_observer.py:384. Re-label candidate, not migration candidate.
- **Misfiled #2: `min_interval_seconds` — config.py:3434**, lives in **NotifyUserConfig** (:3402); env is `ENSEMBLE_NOTIFY_USER_MIN_INTERVAL_SECONDS` (its description :3442) — not a SERVICES_ knob at all. Re-label candidate.
- Other SERVICES_* present (out of the 12 but reachable): DRIFT_RECONCILE_ZOMBIE_CLOSE_* :2552-2615; MISSION_STRAND_HEAL_RUNNING_ZOMBIE_* :2972-3039; UPGRADE_JOURNAL_SWEEP_/REAPER_ :2793-2823; TASK_HEARTBEAT_INTERVAL_SECONDS :2651; STALE_TASK_RECOVERY_* :2218-2246; SERVICE_TOOL_RECONCILE_INTERVAL_SECONDS :3193; SOURCES_GC_DRY_RUN/TERMINAL_RETENTION_DAYS/BATCH_CAP :3039-3112; F1_TREE_ACTIVITY_MAX_AGE_SECONDS (api.py:4805); REPORT_DELIVERY_RECOVERY_LANE_* (config.py:2397).

---

## 4. OFF-LOOP DISCIPLINE SITES (execution classes)

- **MAIN_LOOP precedent:** the agent turn loop = LangGraph graph in `daemon/graph.py` — `StateGraph` import :19; turn-executing node factory `create_agent_node` :8739; middleware slots: InjectionSlot :314, ReportInjectionSlot :697, ContextSlot :775, ToolThrottleSlot :1095, LoopBreakerSlot :1132, TurnRepairLatch :1175, WatchoverSlot :1219. Turns execute on the event loop via the worker pool ⇒ engine MAIN_LOOP class must admit pure in-memory jobs only (decision-report §B.2).
- **DB_OFFLOAD precedents:**
  - Estate-wide idiom: `await asyncio.to_thread(...)` wrapping every sync repo call (e.g. auth_service.py:254-552; maintenance.py:645/:661; api.py:1734, :2990, :3012; bash_spawn_reaper.py:1134-1956).
  - Dedicated bounded pool: `daemon/services/db_offload.py` — ThreadPoolExecutor **6 workers**, `thread_name_prefix="db-offload"` (:5, :75); acquire/permits/inflight :106-253; configured at boot api.py:940-944 (`LIMITS_DB_OFFLOAD_*`).
- **DEDICATED_EXECUTOR precedents (per-service loops):**
  - OrphanWatcherSweepService — asyncio task `create_task(..., name="orphan-watcher-sweep")` :168-171; idempotent start :153-166; stop cancel+await 5s budget :177-198; `sweep_once()` public :200+.
  - SourcesGcService — `asyncio.create_task` :210; `while not self._stopping: await sweep_once(); await asyncio.sleep(interval)` :250-252; first sweep BEFORE first sleep :188.
  - RDRS (report_delivery_recovery) — census TH (dedicated daemon thread + to_thread bridge ≤8s/step); wiring in worker pool.
  - bash-reaper — `daemon/services/bash_spawn_reaper.py` (to_thread DB steps :1134, :1213, :1365-1518, :1948-1956; kill-switch-OFF = silent no-op path per census A2).
  - Checkpoint sweep — `daemon/services/checkpoint_prune.py` (+ `daemon/checkpoint_metrics.py` sweep-lane vocabulary; CHECKPOINT_TTL_HOURS 168h / 24h sweep cadence per core blueprint).
  - tmp-image — `tmp_image_cleanup_service.py:89`.
- **Top-5 periodic mechanisms (census F1 rows 1-6), spawn/loop/knob re-anchored:**
  1. **JobLockSweepService** job_lock_sweep.py:93 — loop :234-237 (`while not self._stopping: await self.sweep_once(); await asyncio.sleep(self._interval_seconds)`); **Template-B PEXIT intact :241-250** (unexpected loop error → log + `return`, next start() re-spawns); knob config.py:2771 read api.py:1967 (DEFAULT_JOB_LOCK_SWEEP_INTERVAL_SECONDS clamps :1963-1985); NO kill switch (always-on hard policy).
  2. **WatchReconcileSweepService** watch_reconcile_sweep.py:91 — knob config.py:2906 read api.py:2023 (DEFAULT clamp :2019-2034); NO kill switch (api.py:1985-1991 @ census).
  3. **EligiblePendingSweepService** eligible_pending_sweep.py:72 — knob config.py:2664 read api.py:1846 (floor guard).
  4. **OrphanWatcherSweepService** orphan_watcher_sweep.py:85 — knob config.py:2695/:2716 read api.py:1916/:1919; born-dead trap intact (see §2.15).
  5. **RetryScheduler** retry_scheduler.py — wiring api.py:1662-1676 (`await retry_scheduler.start()` :1673; `poll_interval=60.0` :1669; file-lock cross-process singleton); kill switch `job_retry_scheduler_enabled` default None=DISABLED config.py:3315 read api.py:1664.
  6. (bonus) **DriftReconcile** — def `_periodic_drift_reconcile_loop` api.py:4758, invoked :1809, interval :1756; env kills `ENSEMBLE_ORPHAN_F1_ENABLED` / `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` / `drift_reconcile_zombie_close_enabled` config.py:2552.

---

## 5. KILL-SWITCH + DUAL-PATH PRECEDENT

- **Config kill-switch pattern (env/yaml tier):** `sources_gc_enabled` config.py:3033 → ctor param `enabled=` sources_gc_service.py:104 → OFF logs ONE INFO line and never sweeps :203 ("(SERVICES_SOURCES_GC_ENABLED=false) — no sweeps will run"); read api.py:2143. Same shape: `service_tool.enabled` config.py:2120 → api.py:3774-3789 ("OFF = the sweep never starts... Byte-identical to pre-Phase-1 behavior" :3775-3777; None-manager guard :3782-3789); `stale_pause_sweep_enabled` :2426 → api.py:3644. **FINEST dual-path precedent: report_delivery_recovery per-lane switches** config.py:2297-2424 (7+1 lanes, master + per-lane) — lane order fixed, each lane independently flippable.
- **DB-backed daemon-settings surface (runtime flags):**
  - `daemon/services/settings_service.py` — `MANAGED_KEYS` registry :253 (`ManagedKeySpec` :139; registry-gated writes; ⚠️ internal doc drift: "NINE managed keys" :80 vs "EIGHTEEN entries" :91); `SettingsService` :1124 (`get_overrides` :1157, `set_override` :1164 — raises `UnknownManagedKeyError` :119 for unregistered keys, `SettingsValidationError` :127; `clear_override` :1195). **Resolution ladder DB > env > yaml > registry default** (:57); env tier = boot seed only, DB wins from first boot (:23-27 in decision/kill.py).
  - Routes `daemon/routers/settings.py`: PUT `/api/settings/daemon/{key}` :966-1017 — `scope_required("settings:write")` :969; 404 unregistered :986-990; 422 invalid :994-1003; sync write off-loop `await asyncio.to_thread(service.set_override, ...)` :1005; audit line `[Settings] db override set key=... value=... subject=...` :1011-1016; DELETE :1020-1034; GET listing :955-963. Scopes: daemon/auth/scopes.py:20, :109-115.
  - **Kill-channel precedent** `daemon/services/decision/kill.py` (:1-67): settings:write-scoped store, LIVE READ per consult (flip = next consult, no restart), **FAIL-CLOSED polarity** (unreadable ⇒ OFF, never fails to ENABLED), **WRITE → READ-BACK → VERIFY**; `set_override` is sync, run off-loop via `daemon.services.db_offload.offload` (:44-47); resolved-state cache invalidated by the write path (:49-63).
- **`*_USE_*` dual-path flags: NONE exist** in daemon/ (only an unrelated `COMPUTER_USE_MCP_ENABLED` comment, mcp_service.py:823). `SERVICES_<MODULE>_USE_ENGINE` would be a NEW family. Closest mechanical models:
  1. constructed-but-stopped OFF semantics (service_tool, api.py:3774-3789);
  2. per-lane enable flags (report_delivery_recovery_lane_*);
  3. decision/kill.py DB-tier flag with fail-closed polarity + cache invalidation.
- **FAIL-TO-LEGACY re-route mechanics (existing polarity to invert):** DependencyBus init treats None as "hard error (no fallback)" api.py:5355-5356 — call sites MUST NOT silently continue. A fail-to-legacy route inverts this: `SERVICES_<MODULE>_USE_ENGINE` OFF ⇒ `get_engine()` returns None ⇒ the call site runs the module's legacy loop; both paths compiled, flag selects (decision-report §B.2:124: "rollback is one flag, not a code change"). Per decision-report B.1, dependencies must resolve via singleton getter AT TICK TIME (never constructor capture) so the flag flip re-routes without restart — the OrphanWatcher born-dead trap (§2.15) is the counter-example the getter discipline exists to prevent.

---

## 6. EVENT EMISSION SITES (registry grounding)

| Site | Current anchor | Primitive |
|------|----------------|-----------|
| `DependencyBus.fire_for_terminal_child` | dependency_bus.py:**1921** (UNCHANGED) | durable row + `transition_state` guarded UPDATE (exactly-once; FIRED rows recoverable) |
| Bus invocation (terminalization wiring) | child_reports.py:**6444 / 6643 / 6945** — all `await self._fire_terminal_child_and_deliver(` (UNCHANGED); wrapper def :741 → bus call :828; sibling wrappers `_emit_terminal_via_bus` :423 (called child_reports.py:1441, :5839, :6677 + error_reporting.py:612) and `_emit_terminal_for_child_instance_via_bus` :651 (called :5844, :6504, :6711, :7052 + error_reporting.py:654) | durable watcher rows, CAS exactly-once |
| error_reporting wiring | error_reporting.py:**693** `_fire_terminal_child_and_deliver` (UNCHANGED); import seams :258-259, :575-580 | same |
| `EventBus.create_event` | event_bus.py:**155** (UNCHANGED); `subscribe_all` :286; ⚠️ archived twin at daemon/_archived/event_bus.py:161/:292 — import path MUST be `daemon.services.event_bus` | durable row + global in-proc subscribers (JobFeedbackObserver pattern — observer constructed api.py:2958-2965 with `manager._event_bus` :2960, started :2966) |
| `[JOB_EVENT]` envelope | work_notifier.py: builder **:285-313** (header `[JOB_EVENT] Job {work_id[:8]}... {status_display}` :300; census 275-313 — small shift), canonical source `_job_event_source` :256-272 (`internal_agent:job_event:{work_id}:{status}` :272), `notify_work_watchers` **:316** | message-envelope trigger; exactly-once CAS delivery (parser keys off `job_event` tag :81) |
| Subscription surface | `DependencyBus.watch` dependency_bus.py:**677** (CAS subscription over dependency_watchers); `start()` :2260 (warm cache + `_recover_fired_unsent` :3004 — crash-safe exactly-once, dedup = `enqueued_at IS NULL`) | durable rows + CAS |
| DEBUG-only consumers (GAP-13) | instance_lifecycle.py:**5976** (pause transition outbox DEBUG log) and **:6589** (resume transition outbox DEBUG log) — BOTH UNCHANGED; outbox machinery :740-874 (NamedTuples), post-commit outbox firing :3004+ | post-commit outbox → SSE / wakeup; consumers DEBUG-only ⇒ nothing hooks it today |
| Durable state-table triggers | `report_injections` PENDING obligations + `mission_commissions` transitions (migrations 20260819_000001, 20261025_122700) | state-table trigger class (RDRS formalization per §B.3) |

---

## 7. LANDED v0.31.11 TELEMETRY (`job_metrics` family)

All in `daemon/services/maintenance.py`:
- `MaintenanceJob` dataclass :230 — `max_staleness_hours` :245, `last_success_ts` :248, `last_attempt_ts`, `consecutive_skips` :256; `JOB_MAX_STALENESS_HOURS` built-ins :209; `IdlePolicy` :105 (UN_GATED vs IDLE_GATED tiering).
- `MaintenanceService` :259 — `start()` :415, `stop()` :439, `_loop()` :456 (60s boot-stabilization sleep :465 — NOT the ordering guarantee; ordering is structural via `maintenance_boot_sweep.run_boot_sweep_with_retry` in `manager.initialize()`, :458-464), `_run_pending_jobs()` :475.
- **Honest-stamp shape (the permanently-due fix, v0.31.11):** stamp ATTEMPT before the call :524-528 (`job.last_attempt_ts = utcnow()`); on success stamp `last_run` :530 + `last_success_ts` :531 + `consecutive_skips = 0` :532; every skip goes through `_record_skip` :568-601 (`job.consecutive_skips += 1` :579 + log with reason). `forced` override only for IDLE_GATED :500-509; escape warning :510-521.
- Staleness escape `_is_stale` :538-567 — reference clock order: `last_attempt_ts → last_success_ts → last_run → self._started_at` (:561-566; boot anchor so fresh installs don't instantly fire under load).
- Restart-clock invariant `hydrate_persisted_state` :624-687 — persisted last-success from `maintenance_runs` audit rows is source of truth (:627-631, wi-4d1fa093); persisted `max_staleness_hours` override wins :633-634 but only applies to IDLE_GATED jobs :671-687; all DB reads via `asyncio.to_thread` :645/:661; fail-soft per job :636-638.
- **`job_metrics()` :603-622** — per-job dict with exactly two labels today: `last_success_ts` (ISO-8601 UTC | None) + `consecutive_skips` (int). Standing observability surface for the starvation class (:604-611).
- Engine extension (next_due, last_duration_seconds, last_outcome, skips_total{reason}, dead=true) generalizes THIS family — no parallel family (decision-report B.1:106). Persistence precedent: `maintenance_runs` table (migration 20260927_000001_create_maintenance_runs_table.sql:22) + `MaintenanceRunLock` single-flight (maintenance_run_lock.py:52 per decision-report).

---

## 8. GREP-FENCES ON CANDIDATE SUBSTRATES

- **`_NAME_RE` dormancy fence (THE model for engine fences):** `tests/test_resource_control_seam.py`
  - `TestDormancyWriteSiteAudit` :294; `_NAME_RE = re.compile(r"\b(action_by|acted_at)\b")` :300.
  - `test_no_daemon_file_names_action_by_acted_at` :302-321 — word-boundary grep over EVERY `daemon/**/*.py`; the dormant names may appear ONLY in `daemon/models/resource_monitor.py` (:299 `_MODEL_REL`). **HARD fence** (`assert violations == []`). Friction contract: "Any future write site must consciously edit these audits" (:295-297).
  - `test_no_resource_pid_map_string_names_action` :323-346 — string-token pass (docstrings skipped :337-339) forbids any string literal combining `resource_pid_map` + `action` (raw-SQL write-drift guard). **HARD.**
  - `test_flusher_pid_map_insert_excludes_dormant_columns` :348+ — AST pin on the only write seam. **HARD.**
- **Zero existing fences on the new names:** greps across `tests/` for `engine_jobs`, `engine_runs`, `ENSEMBLE_ENGINE`, `job_metrics` return NOTHING (all hits were case-insensitive false positives on `name_re*`). The names are unclaimed — no fence to break, but the _NAME_RE pattern above is the house style for adding one.
- **Boot-sweep pins the engine boot phase must not break (all HARD / pack-gated):**
  - `tests/test_opt_o8_stale_subprocess_sweep.py` (:33-40) — behavior pins for `sweep_stale_subprocesses` incl. the default-ON pin (`test_default_is_on_pinned`, referenced boot_subprocess_sweep.py:103-105) and env-override pin (`test_env_override_wins_in_dev_test`).
  - `tests/test_bind_retry.py:262-293` — sweep-related pin.
  - `tests/helpers/ram_guard_fence.py:8/:30` — test-fence helper: pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` EXPLICITLY (the critical-notes fence — `env -u` strips the ambient guard).
  - `tests/packs/ram_guard_phase6_mutation_matrix.py:27/:299` — pack-level pin of the same var.
- **Readyz / readiness pins:**
  - `tests/test_health_probes.py` (incl. `test_refresh_services_component_degrades`).
  - `tests/unit/tools/test_readyz_grace_detail.py`.
  - `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py` :11/:158 — boot-epoch → readyz queue_freshness amnesty contract (the 120s breach point).
  - `tests/e2e/test_auth_lifecycle.py:142-166/:443` — /readyz in auth lifecycle.
  - `tests/unit/services/test_boot_epoch.py` — epoch capture pins.
  - `tests/integration/test_boot_report_recovery.py`, `tests/unit/services/test_boot_orphan_sweep.py` — boot-pass behavior.
- **Queue invariant to respect:** job_queue membership filters must route through `ACTIVE_ADMISSION_STATES` (models.py:51-58) — an engine_jobs table that joins job_queue_items must not hand-roll `IN ('queued','active')` literals.

---

## 9. ANCHOR DRIFT (census/decision-report @ `bf4bfde6e` → this tree `b71742fe3`)

### MOVED (old → new)
| Anchor (census/report) | This tree |
|---|---|
| config.py:2709 (job_lock_sweep_interval_seconds) | **2771** |
| config.py:2844 (watch_reconcile_sweep_interval_seconds) | **2906** |
| config.py:2602 (eligible_pending_sweep_interval_seconds) | **2664** |
| config.py:2633 (orphan_watcher_sweep_interval_seconds) | **2695** |
| config.py orphan grace field | **2716** |
| config.py:2184 (drift_reconcile_interval_seconds) | **2246** |
| config.py:3253 (job_retry_scheduler_enabled) | **3315** |
| config.py:3158 (lease_heartbeat_interval_seconds) | **3220** |
| config.py:1286 (checkpoint_interval) | **1309** |
| config.py:2086 (worker_poll_interval) | **2148** |
| config.py:3372 (min_interval_seconds) | **3434** |
| config.py:3251 (observer_health_check_interval_seconds) | **3313** |
| api.py:1920 (OrphanWatcher capture) | capture/construct block **1910-1931** (ctor :1921) |
| api.py:2972 (init_dependency_bus) | **2974** |
| api.py:5710 (set_dependency_bus) | **5712** |
| api.py:1647-1661 / :1650-1657 (RetryScheduler wiring / 60s poll) | **1662-1676** (poll_interval=60.0 :1669, start :1673) |
| work_notifier.py:275-313 ([JOB_EVENT] builder) | builder **285-313** (header :300); `_job_event_source` :256-272; `notify_work_watchers` :316 |
| eligible_pending_sweep.py:71 (class) | **72** (1-line shift) |
| migration census era "119 files" (blueprint) | **134 files** (FA-2 census; the study-note 127 was itself stale) |

### UNCHANGED (verified identical)
dependency_bus.py:1921 (fire_for_terminal_child) · event_bus.py:155 (create_event) + :286 (subscribe_all) · child_reports.py:6444 / :6643 / :6945 (bus wiring call sites) · error_reporting.py:693 · instance_lifecycle.py:5976 / :6589 (DEBUG-only consumers) · job_lock_sweep.py:93 (class) + :241→:250 (Template-B catch→return) · watch_reconcile_sweep.py:91 · orphan_watcher_sweep.py:85 · sources_gc_service.py:96 (+ loop :250-252) · job_state_machine VALID_TRANSITIONS 8-entry set.

### SUPERSEDED (not drift — fixed by v0.31.11)
decision-report B.1 cites maintenance.py:260-269 as the permanently-due trap — those lines no longer exist; the landed shape is stamp-on-attempt :524-528, stamp-on-success :530-532, `_record_skip` :568-601 (§7 above).

---

## Open questions
1. **RDRS interval read site:** `report_delivery_recovery_interval_seconds` (config.py:2267) is consumed by the worker-pool wiring per census (pool_orchestrator.py:363-538 @ census). Not re-grepped in pool_orchestrator.py at this tree — plan should re-grep before pinning that lane's read-site anchor.
2. **"~11 awaited passes" accounting:** the lifespan contains ~15 awaited/timed boot calls (§2 list). The census's "~11" maps to a subset (likely excluding to_thread one-shots and the readyz refresher). Planning should state its own explicit list rather than inherit the count.
3. settings_service.py internal doc drift ("NINE" :80 vs "EIGHTEEN" :91 managed keys) — count the live `MANAGED_KEYS` registry at :253 when sizing a new engine key registration.
