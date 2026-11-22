# Census — Daemon-Side PERIODIC / POLLING / SWEEP / RECOVERY Machinery (CODE TRUTH)

**Study:** central polling/event-management engine decision, Phase 0 · **Lane:** periodic-machinery census
**Repo:** `/home/nea/ensemble-src` — canonical checkout, detached HEAD `bf4bfde6e` (describe: `v0.30.9`), read 2026-11-21. Tree state: 4 tracked mods (2 known landed-latest echoes: `worker_pool.py`, `task/repository.py`; 2 agent-runtime files) — none in census scope. LIVE daemon runs v0.31.22; this census documents CODE at `bf4bfde6e`. Known skew risks flagged inline (⚠).
**Method:** 3 parallel worker investigations (maintenance tier / boot+ sweeps / hunt) + controller spot-checks of headline claims (JobLockSweep exit block, maintenance skip-trap, OrphanWatcher wiring, bash reaper lanes — all personally re-read). All anchors are fresh greps at this commit. A concurrent L1 lane edits gzip/orjson/logging code; no mid-edit artifact was observed in any census target (one 🟡 noted in F8).
**Companion docs:** live-execution evidence = sibling lane (maintenancer); event-source census = sibling lane (census-events). FE's 16 pollers OUT of scope by commission.

**Row counts (CANONICAL, re-derived from the §1 tables per review 2026-11-21; header must equal table):** 67 table rows = **66 distinct mechanisms** in 13 families (row 65 duplicates row 21's bash boot reconcile, marked "= row 21") · **15 boot-only rows** (rows 17-31) · 3 dormant-trapped/dead mechanisms · **3 dead/vestigial config knobs + 2 non-periodic knob reclassifications** (§8) · 11 found beyond the seed leads.

---

## 1. MASTER TABLE

Trigger codes: **FI**=fixed-interval · **FTI**=fixed-interval+first-tick-immediate · **IDLE**=idle-predicate-gated · **EVT**=event-driven · **BOOT**=boot-only · **OD**=on-demand-only (negative result) · **COND**=conditional/per-entity
Fail codes: **L&C**=logs-and-continues · **PEXIT**=PERMANENT loop exit on unexpected Exception · **FO**=fail-open · **NOOP**=silent no-op path exists
Topo codes: **AL**=main asyncio loop (lifespan task) · **TH**=dedicated daemon thread · **TT**=asyncio.to_thread bridge

### F1 — Job / lock / watch reconciliation
| # | Mechanism | file:line (class / loop) | Trig | Cadence (key, default) | Fail | Kill switch | Topo | Guards / Observability |
|---|---|---|---|---|---|---|---|---|
| 1 | JobLockSweepService | job_lock_sweep.py:93 / :232-252 | FTI | 90s `job_lock_sweep_interval_seconds` (config.py:2709) | **PEXIT** (:241-252) + tick-level L&C (:203-214) | NONE — always-on hard policy (:60-64) | AL | atomic `DELETE…NOT IN(active)`; rowcount log only (:216-220), NO counters |
| 2 | WatchReconcileSweepService | watch_reconcile_sweep.py:91 / :365-424 | FTI | 300s `watch_reconcile_sweep_interval_seconds` (config.py:2844) | L&C (:405-409; GC leg :294-300) | NONE — always-on (api.py:1985-1991) | AL | CAS exactly-once via `notify_work_watchers`; zombie-GC leg; counters `fired/retired/scanned/stored_*` + cumulative (:344-355) |
| 3 | EligiblePendingSweepService | eligible_pending_sweep.py:71 / :420-447 | FTI | 90s `eligible_pending_sweep_interval_seconds` (config.py:2602); min-pending-age 60s (:68) | L&C (double fence :435-443) | none (floor guard api.py:1877-1881) | AL | W-D liveness filter (api.py:1848-1858); logs |
| 4 | **OrphanWatcherSweepService** †DORMANT-TRAPPED | orphan_watcher_sweep.py:85 / :272-296 | FTI (but see depth row A1) | 90s `orphan_watcher_sweep_interval_seconds` (config.py:2633); grace 30s (:82) | L&C (:280-290) | NONE (api.py:1906-1911) | AL | **`dependency_bus` permanently None in prod** — see depth row A1 |
| 5 | RetryScheduler | retry_scheduler.py / :154-163; wiring api.py:1647-1661 | FTI | 60s poll (api.py:1650-1657) | L&C | `job_retry_scheduler_enabled` default **None=DISABLED** (config.py:3253) | AL | file-lock cross-process singleton; `_release_scheduler_lock` |
| 6 | DriftReconcile loop (JobRecoveryService) | api.py:4743-4856 (task :1793-1808); service job_recovery_service.py | FI, first tick +30s (api.py:4804-4809) | 300s `drift_reconcile_interval_seconds` (config.py:2184) | L&C (:4885-4897) | `ENSEMBLE_ORPHAN_F1_ENABLED` (:175), `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` (:296), `drift_reconcile_zombie_close_enabled` (:2490) | AL | pattern-(h) census-registered single txn; counter block when examined>0 (api.py:4850-4874) |
| 7 | JobProcessor dispatch | job_processor.py:679-686 (`wait_for_job(timeout=30)`) | EVT | n/a (bus wait) | L&C | n/a | AL | dispatch_event_bus |

### F2 — Report delivery recovery (RDRS)
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Guards / Observability |
|---|---|---|---|---|---|---|---|---|
| 8 | ReportDeliveryRecoveryService — 8 lanes | report_delivery_recovery.py:714 / :1001-1016; wiring pool_orchestrator.py:363-538 | FTI + boot sweep (to_thread :476-492) + on-demand `recover_now()` :1041 | 300s `report_delivery_recovery_interval_seconds` (config.py:2205); batch 100 (:2220); retry 1min (:2227) | L&C (:1009-1012); per-row invariant sets | master `report_delivery_recovery_enabled` (config.py:2196) + **per-lane switches (7+1)** (:58-61) | TH (+TT bridge, ≤8s/step :952-960) | lane order :1147-1186: 1 DEFERRED→2 NO-ROW→3 PENDING-AGE→4 RECOVERY-RETRY→5 ORPHAN→6 STUCK-WAKE→**wc_orphan_detector (observability-only, 8th undocumented in docstring)**→7 MISSION-RECONCILE last. 🟡 docstring says "seven lanes" (:22,:57); wiring carries 8 (pool_orchestrator.py:399-421) |

### F3 — Mission / strand healing
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Guards / Observability |
|---|---|---|---|---|---|---|---|---|
| 9 | MissionStrandHealer | mission_strand_healer.py:234 / :372-390 | FTI + event-driven heal (child_reports) | 300s `mission_strand_heal_interval_seconds` (config.py:2876) | per-tick isolation L&C (:377-383); invariant conjunctive fail-closed (:637,:843) | always-on; family-3 switch `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED` (config.py:2910, d=True), age 7200s (:2922), batch 25 (:2945) | AL | **refcounted per-root heal locks** (:471-503) vs event path |
| 10 | WaitingChildrenWatchdog | waiting_children_watchdog.py:449 / loop :1756-1816 (task api.py:2475-2483) | FTI | 3600s `waiting_children_watchdog_interval_seconds` (config.py:2557); hang threshold 3600s (:2571) | L&C (:1808-1815) | `waiting_children_watchdog_enabled` (config.py:2547, d=True) | AL | wedge pass inside `run_once` (:1467+), re-arm cooldown 1800s (:194), WEDGE_SOURCE `system:watchdog:wedge` (:180); cooldown sets + live-carrier checks (:707-830); counters (:648-706). **The parent-of-working-child false-positive class lives here** (:17, `last_activity_at` staleness) |
| 11 | mission_live_guard | mission_live_guard.py — NO loop; `evaluate_mission_live` :327 | **OD** (negative) | ticks nowhere; callers: job_queue_service :589/:609, drift (h) :5059/:5206, healer :644/:1159, RDRS :3112, child_reports ×4, work_notifier :97 | n/a | n/a | caller's | 6h backstop anchored over NON-ROOT members (:338-356); probe error ⇒ HOLD |

### F4 — Maintenance tier (CRITICAL DEPTH — see §3)
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Guards / Observability |
|---|---|---|---|---|---|---|---|---|
| 12 | MaintenanceService (generic scheduler) | maintenance.py:102 / `_loop` :235 (sleep :244 = 60s pre-first), `_run_pending_jobs` :254 | FI tick + **IDLE gate** (:260-265) + per-job `_is_due` (:297-323) | 15-min tick `maintenance_check_interval_minutes` (config.py:1289); per-job `min_interval_hours` 1–24 | L&C (:246-253); execute-fail keeps last_run stale (:271-274) | scheduler: none; per-job knobs (interval≤0 emergency stop :303-307) | AL (+TT for DB) | skip log :264; `last_run` in-memory only; **no scheduler-level counters** |
| 13 | CheckpointCleanupJob (ops A–E) | maintenance.py:772 / execute ~:881 | IDLE+interval | 24h `checkpoint_cleanup_interval` (constants.py:171, config.py:1288) | per-op isolation; audit row running→succeeded/failed/interrupted | `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0` | AL (+TT; dedicated autocommit engine for ANALYZE) | MaintenanceRunLock + `uq_maintenance_runs_running_section` (migration 20260927_000001); metric `pinned_subtree_terminal_count` (:900-907) |
| 14 | VacuumMaintenanceJob | vacuum_maintenance.py:151 / execute ~:205 | IDLE+interval | 24h `vacuum_interval_hours` floor 0.25 (config.py:1301-1306); gate `n_dead_tup >= max(1000, 5%·n_live)` | per-table WARNING continues; cycle never raises | `PERSISTENCE_VACUUM_ENABLED=0` skips registration (manager.py:4601-4614) | AL (+NullPool autocommit engine/cycle) | per-table timeouts: stmt 10min / lock 45s / idle-txn 60s (constants.py:584-586); VacuumCycleSummary; audit section='vacuum-maintenance' |
| 15 | Blob prune Op E + per-thread Op D | checkpoint_prune.py:164; maintenance.py:1466 | invoked by row 13 | keep-N 3/thread floor 1; batch 1000 SERIALIZABLE deletes | per-pair fail-safe skips (ZERO_REFS_FAIL_SAFE, MAX_REFS_EXCEEDED) | destructive-by-default since v0.20.0 (no env gate); manual `/dry-run` opts out | AL (+TT) | BlobPruneSummary counters; partial_pairs surfaced |
| 16 | 7 other registered jobs | manager.py:4700-4785, :4641-4657, :4667-4682, :7518-7520 | IDLE+interval via row 12 | skill_metric_scan 24h (config.py:6411) · skill_orphan_sweep 24h · project_skill_gc 24h (`PROJECT_SKILL_GC_INTERVAL_HOURS` manager.py:303) · blueprint_daily_scan **5h** (last_run in project KV) · malloc_trim 1h · dependency_bus_lock_reap 1h · anomaly_retention 24h | via row 12 | per-job gates (skill_evolution, _project_skill_source) | row 12 | **no maintenance_runs audit rows for these 7** — skip storm visible only in logs |

### F5 — Boot-only recovery passes (fire once per boot; see boot map §5)
| # | Mechanism | file:line | Notes |
|---|---|---|---|
| 17 | BootSubprocessSweep | boot_subprocess_sweep.py:356; __main__.py:567-580 | PRE-config/pre-uvicorn; TERM→grace→KILL; fail-open ×2; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` lane-default (prod ON / source OFF, :183-226) |
| 18 | MessageQueueBootRecovery | message_queue_boot_recovery.py:63-103; api.py:966-971 | boot-epoch-relative predicate (:20-26); reset-only; `ENSEMBLE_HARD_REBOOT_BOOT_PASS` |
| 19 | ExecutionGate `recover_stale_leases` | api.py:983-995 | awaited; lease heartbeat config is DEAD (see §8) |
| 20 | JobLockManager boot recovery | api.py:1150-1163 | awaited |
| 21 | Bash boot reconcile + retention prune | api.py:1178-1213; bash_spawn_reaper.py:1432 | lane deadline 60s; prunes reaped rows >30d (:1515-1527) — **only retention GC that exists is boot-only** |
| 22 | delete_doomed arm | api.py:265-275, task :1069-1077 | readyz-green AND +1s OR +5s; 5-min DELETE timeout |
| 23 | JobRecoveryService.recover_on_startup | api.py:1689-1693 | awaited |
| 24 | Snapshot boot sweep | api.py:1714-1729 | to_thread |
| 25 | RDRS boot sweep | pool_orchestrator.py:476-492 | fire-and-forget to_thread |
| 26 | DependencyBus.start() recovery | api.py:2959→:5332/:5358; dependency_bus.py:1699 | warm cache + FIRED-unsent + **one-shot orphan sweep :1753** (see A1) |
| 27 | UpgradeJournal boot reconcile + `sweep_wake_records` | api.py:~3510-3535 (:3525); task :3658 | awaited, fail-open (:3646-3651) |
| 28 | AutoContinue boot pass | auto_continue_boot_pass.py:423+; api.py wiring block :3552-3580 (call :3575) | stagger bounds LLM stampede (:269-271); CAS `auto_continued_at` (:9-13); `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` |
| 29 | sweep_orphaned_live_turns | auto_continue_boot_pass.py:918; api.py call :3580 (block :3552-3580) | checkpoint-epoch gate; SYNTHETIC work_id; graceful-restart class CLOSED |
| 30 | StalePause boot pass | api.py:3625-3645 | distinct from periodic thread; shares `stale_pause_sweep_enabled` |
| 31 | ServiceReconciliation boot sweep | api.py:3795-3800 | awaited; "never aborts boot" |

### F6 — Pool / task lifecycle
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 32 | StaleTaskRecovery | stale_task_recovery.py:43 / :200-214; wiring pool_orchestrator.py:285-315 | FTI | 60s (:26); stale 15min (:25); retry backoff 60→3600 max 3 (:28-30) | L&C (:207-208) | none found | TH | boot-epoch amnesty interplay w/ AutoContinue |
| 33 | StalePauseSweepService | stale_pause_sweep.py:479 / :546-556; wiring pool_orchestrator.py:343-350 | FTI | 300s `stale_pause_sweep_interval_seconds` (config.py:2372); age 7200 (:2381); batch 100 | "never kill the thread" (:550-555) | `stale_pause_sweep_enabled` (config.py:2364, d=True) | TH | fail-open release semantics (:175,:209) |
| 34 | TaskHeartbeat | worker_pool.py:60 (per worker :286-289, :300) | FI per-worker | 30s `task_heartbeat_interval_seconds` (config.py:2589); eager beat on claim (:114-118); idle ⇒ no writes (:107-110) | thread death ⇒ stale ⇒ stale-task force-cancel (:71-78) | lifecycle-bound | TH ×N | atomic UPDATE last_heartbeat_at |

### F7 — Source adapters / scheduler substrate
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 35 | SchedulerAdapter (scheduler sources) | sources/adapters/scheduler.py:703-845 | COND per-source | cron / `interval_seconds` / one-shot `run_at` (:512-563); one-shot re-arm cadence **5s** (:820-842); error retry sleep 5s | per-fire; deferred-delivery latch `_defer_*` (:1166-1202) | `cancel_schedule` (scheduling_service.py:859-887) | AL per source | lateness cap one_shot_max_lateness ⇒ SKIPPED row (:713-741); W1 once-per-run_at |
| 36 | Adapter supervisor (all sources) | sources/registry.py:838-875 | COND | restart backoff 2s→300s cap; 60s-success reset (:849-861); start timeout 60s (:160) | restart loop | per-source enable | AL | per-source asyncio.Lock (scheduling_service.py:279) |
| 37 | SourceCleanup | sources/cleanup.py:20-22 / :48-56; manager.py:16384 | FI, **sleep-first 60s** | 6h; processed-msg TTL 24h; session-map TTL 30d (:63-71) | L&C | none found | AL | deliberately NOT at boot |
| 38 | SourcesGcService | sources_gc_service.py:96 / :240-262; wiring api.py:2136-2148 | FTI | 3600s `sources_gc_interval_seconds` (config.py:2992); retention 14d (:3005) | **PEXIT** loop-level (:256-262); per-row isolation (:391) | `sources_gc_enabled` (config.py:2971, d=True) | AL | dry-run ticks ALWAYS log counts (:336); collectable predicate at repo layer |

### F8 — Resource / telemetry / metrics
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 39 | Resource-monitor sampler | resource_monitor/sampler.py:291+ | FI | 30s `sample_seconds` [15-60] (config.py:998); **self-throttling**: overrun ⇒ double interval + deep-scan off; EMA cost >1% ⇒ double (:10,:41) | build-fail ⇒ subsystem disabled, boot continues (facade.py:393-397) | `RESOURCE_MONITOR_ENABLED` (config.py:~994) | TH | DEGRADED-SENTINEL rows on lost windows (sampler.py:161-167) |
| 40 | Resource flusher + anomaly piggyback | flusher.py:1039-1051; anomaly resource_anomaly.py:1732-1776 (facade.py:325-345) | FI | flusher wait-sliced on `flush_batch_seconds`; anomaly gate 600s `anomaly_interval_s` [300-900] (config.py:1096) — **no own thread, piggybacks flusher** (:1729) | anomaly fail ⇒ layer disabled only (facade.py:376-381) | `RESOURCE_MONITOR_ANOMALY_ENABLED` (config.py:~1091) | TH | |
| 41 | RamGuard | ram_guard/guard.py:166+ / :472-484; wiring api.py:4646-4663 | FI | **200ms** `poll_interval_ms` (config.py:1245 d=200; guard.py:180 d=200) — ⚠ commission said "~1s"; code says 200ms at bf4bfde6e (live env may override; L1 lane may be changing it) | boot-fail ⇒ guard OFF, daemon continues (api.py:4665-4680) | enabled/platform gate (api.py:4630-4641) | **TH ×2** (tick + kill lane) | one-shot PSS confirm 250ms deadline fail-open⇒kill; kill ladder TERM→grace→KILL; per-bucket counters (:113-121) |
| 42 | WedgeCaptureWatchdog | wedge_capture/watchdog.py:190+ (start :263-295; wiring api.py:3680-3700) | FI | 1Hz (`DEFAULT_SAMPLE_INTERVAL_S=1.0`, config.py:103) | **tick path never logs** (:33-38, A4/RK-15); `missed_samples` counter only | `ENSEMBLE_WEDGE_CAPTURE_ENABLED` | TH ("NEVER a coroutine" :7-13) | triggers: sustained_cpu 95%/30s, sustained_loop_lag >5s/15s, thread-conc 85% (triggers.py:53-101); capture deadline 20s; cooldown 300s; max 20 captures/512MB/168h |
| 43 | Metrics listener | metrics_listener.py:329/:621; wiring api.py:2750 | EVT (HTTP pull) | `serve_forever(poll_interval=0.2)` (:621) | registry accumulates while off (:43) | managed-key gated | TH (HTTP server) | |
| 44 | Metrics PG census sampler | metrics_census.py:267 / :457-476; wiring api.py:2783 | FI collect-then-wait | 15s `metrics_census_interval_s` (config.py:841) | whole-tick guard "dead producer" (:459-472) | module metrics gate (:313-336) | TH | **DORMANT-BY-FLAG on live** (census_enabled=false per live lane); non-pooled dedicated conn (api.py:2768-2771) |
| 45 | Metrics trend sampler | metrics_query/sampler.py:65 / :156-168; wiring api.py:2867 | FI | 10s `metrics_sampler_interval_s` (config.py:852); effective = max(sampler, publisher cadence) (provider.py:141) | L&C (:158-159); stuck-thread logged at shutdown (:136-143) | none sampler-level | TH | TrendRingStore |
| 46 | checkpoint_metrics publisher | checkpoint_metrics.py:153 (provider wired api.py:2896-2906) | EVT (increment) + cadence gate | 15s `metrics_scrape_interval_hint_s` (config.py:831) / DEFAULT_PUBLISHER_CADENCE_SECONDS 15 (:128) | n/a | metrics gate module | AL (increment path) | snapshot copy ≤1/window |
| 47 | log_sumup timer | log_sumup.py:284-294 | FI | 180s window `ENSEMBLE_LOG_SUMUP_WINDOW_S` (:5-7) | n/a | n/a | TH (one timer) | O(1) fold/event |

### F9 — LLM / stream
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 48 | LLM stream-stall watchdog | llm_stream_watchdog.py:584-593 (tick const :98); wiring api.py:2560-2597 | FI **sleep-first** | **1s** WATCHDOG_TICK_INTERVAL_SECONDS (:98); stall threshold 45s floor 10 (:76) | degrades to 610s read-deadline (api.py:2566-2569) | **always-on, no disable value** | TH | force-unblock transport (:253,:305); forensic `[LLM-WATCHDOG-STALL]` (:497) |
| 49 | long_tool_nudge loop | long_tool_nudge.py:1207; wiring api.py:2541-2549 | FI | 60s; per-instance thresholds floor 60s (:333) | per-instance isolation; L&C | `LONG_TOOL_NUDGE_ENABLED` d=ON | AL | advisory-only (:38); strict `>` boundary (AD-4) |

### F10 — Readiness / health probes
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 50 | Readiness refresher (+queue_freshness) | api.py:5175 / loop :5257+/:5327; task :3102-3109 | FI **first tick t=0** (:5223-5226) | 10s `readiness_refresh_interval_seconds` (config.py:3170) | per-cycle logged; stale composite serves (:5320-5325) | `READINESS_FORCE_DEGRADED_ENV` (drill) | AL | ONLY DB toucher for /readyz; includes SELECT 1 (500ms budget), queue_freshness (readiness.py:297), checkpoint-saver SENTINEL write (:1961-1982 via :5250-5251), MCP journal drain (:5296-5310) |
| 51 | VSCode server health loop | vscode_server_manager.py:900-930 | COND (per running server) | **2s** `VSCODE_HEALTH_CHECK_INTERVAL_S` (constants.py:644); timeout 10s | failures logged, do NOT flip status (:902-905) | per-server lifecycle | AL | restart backoff 1→30s max 5 (:77-79) |
| 52 | MCP warmup-pool health check | mcp/warmup_pool.py:1170-1195; manager.py:4039 | FI | 60s `health_check_interval` (config.py:5017, mcp_pool) | per-cycle logged, continues | `_running` flip | AL | single-task guard (:1197-1203) |
| 53 | MCP init_guard circuit breaker | mcp/init_guard.py | **OD** (negative — no loop) | half-open admits ONE probe on next tool call after 300s (`INIT_RECOVERY_TIMEOUT_S` :111; probe_claimed :218) | n/a | n/a | caller's | event-driven recovery probe |

### F11 — Memoir workers
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 54a | MemoirEmbeddingWorker | memoir_embedding_worker.py (run loop; config.py:4896); wiring api.py:3153 | FI tick-first | 60s (env re-read per tick); budget 45s/tick (:175) | tick-fail logged, next tick runs; **lane-down latch**: 3 all-fail ticks ⇒ 300s ticks (:170) | memoir boot knob | AL | boot catch-up = first tick (:13-15) |
| 54b | MemoirReflectionWorker | memoir_reflection_worker.py:135-160; wiring api.py:3260 | FI | 30s `tick_interval_s` (config.py:4329, `ENSEMBLE_MEMOIR_REFLECTION_TICK_INTERVAL_S`) | same house contract | memoir boot knob | AL | **Commission lead "constellation" has NO own worker/row — it rides THIS tick**: the reflection service invokes the constellation summarizer per reflection pass (memoir_reflection_service.py:845 `self._constellation_service(now).summarize_qualifying(...)`, guarded `if self._constellation is not None`); constellation_service.py itself has no loop (negative result, Worker C) |
| 54c | MemoirPoignancyWorker | memoir_poignancy_worker.py:233-256; wiring api.py:3326 | FI | 20s (`MEMOIR_POIGNANCY_TICK_S=20`, poignancy_service.py:113); latch 300s (:164) | dead credential stops tick, rows DEFERRED (:42-43) | memoir boot knob | AL | **two scoring threads** so LLM overrun can't block accounting (:20-25) |

### F12 — Bash spawn reaper (†depth row A2)
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 55 | BashSpawnReaper (5 lanes) | bash_spawn_reaper.py (sweep_rows :1145, sweep_instance :1344, boot reconcile :1432, shutdown :1386, enumerated :1926) | **EVT piggyback — NO standalone loop** | fires on: instance terminalization (tools/bash.py:630 via `cleanup_instance`), boot (api.py:1184), shutdown (manager.py:16683+), + enumerated stamp sweep at same sites | **silent no-op** on kill-switch OFF (zero repo access, sweep_instance :1364-1367) or repo None; per-call failures WARNING best-effort (bash.py:636-641) — nothing to "die", but can no-op forever | `kill_switch_from_env` (:139) | caller's (AL/TT) | durable counter `ensemble_sweep_rows_total` (metrics_query/wiring/w2.py:373, .inc :704, lane/outcome labels); retention prune 30d **BOOT-ONLY** (:1515-1527) ⇒ inter-boot unbounded growth structural |

### F13 — Misc housekeeping (found beyond leads)
| # | Mechanism | file:line | Trig | Cadence | Fail | Kill switch | Topo | Notes |
|---|---|---|---|---|---|---|---|---|
| 56 | _cleanup_stale_completions | manager.py:4464 / :4870-4882 | FI **sleep-first 600s** | 600s | L&C | n/a | AL | CompletionRegistry |
| 57 | _cleanup_cached_instances | manager.py:4466 / :7988+ | FI sleep-first 600s | 600s TTL+LRU | L&C | n/a | AL | O3 resource control |
| 58 | TmpImageCleanupService | tmp_image_cleanup_service.py:318-348; wiring api.py:2093-2101 | FTI | 3600s `tmp_image_cleanup_interval_seconds` (config.py:3079); retention 30d (:3098) | **PEXIT** loop-level (:343-348); sweep-fail retries next tick (:290-296) | retention_days lever (config :3076-3078) | AL | reaping ticks log, no-op ticks silent |
| 59 | PlaneSyncWatchdogService | plane_sync_watchdog_service.py:394-420; wiring api.py:2384-2402 | **FTI boot sweep** (:399-403) | 300s `plane_sync_watchdog_interval_seconds` (config.py:2770); per-row backoff 60→1800s max 5 (constants.py:797-817) | per-row counted; loop-level exit (:417-425) | none found | AL | |
| 60 | FileChangeMonitor poll fallback | file_change_monitor.py:203-226 | COND (first subscriber → last removes, :86-92) | `DEFAULT_POLL_INTERVAL_S` (🟡 value not captured; :79-83) | L&C | watchdog backend preferred | AL | |
| 61 | compact_executor heartbeat | compact_executor.py:1697-1730 | COND (during compaction) | 10s `_HEARTBEAT_INTERVAL_S` (:282) | n/a | n/a | AL | reads dispatcher seq at emit (collision-proof) |
| 62 | promote_txn_hold release driver | promote_txn_hold.py:422-423 | COND (while deferrals exist) | 5s `RELEASE_DRIVER_TICK_S` (:71) | stateless skip while live (:91) | n/a | AL | |
| 63 | mcps_gateway mission monitor | (mcps_gateway) :494, clamp :586-590 | COND per-mission | min(0.1s, timeout/10) | n/a | n/a | AL | not daemon-wide |
| 64 | UpgradeJournal reaper worker | upgrade_journal_sweep.py:3087-3090 | EVT (queue) | waitpid bounded, default timeout 660s (:110) | benign-detach NO-kill | n/a | AL | |
| 65 | bash-reconcile boot task | api.py:1178-1213 | BOOT | one-shot, 60s lane deadline | best-effort | n/a | AL | = row 21 |

*Cross-ref: UpgradeJournalSweepService periodic (90s `upgrade_journal_sweep_interval_seconds` config.py:2731, FTI, loop-level PEXIT :3077-3083, + boot reconcile row 27; always-on family no kill switch). ServiceReconciliationService periodic (90s `service_tool_reconcile_interval_seconds` config.py:3131, FTI + guaranteed boot sweep, boot pass never aborts boot api.py:3796-3800, D7 kill-switch `services.service_tool.enabled` OFF ⇒ never starts api.py:3770-3776, PID-liveness per row, counters alive/reaped/errors logged when >0 :316-317).*

---

## 2. DEPTH ROWS — LIVE-EVIDENCE CROSS-FEED (leader addendum 2026-11-21)

### A1 — OrphanWatcherSweepService: DETERMINISTIC WIRING-ORDER TRAP (dormant-trapped, watch-#9-class specimen #2)

**Trap class: WIRING-ORDER (deterministic, every boot). NOT a race, NOT an idle-predicate trap.**

Chain (all personally re-read):
1. `daemon/api.py:1906-1910` — the service is constructed with `dependency_bus=get_dependency_bus()` — the module singleton is read **at construction time** and stored in `self._dependency_bus` (orphan_watcher_sweep.py:138).
2. `daemon/api.py:1921` — `orphan_watcher_sweep.start()` begins ticking (90s, first tick immediate).
3. `daemon/api.py:2959` — `await init_dependency_bus(app, manager)` runs **~1000 lines LATER in the same sequential lifespan body**; it is the ONLY caller of `set_dependency_bus(bus)` (api.py:5697; grep confirms).
4. The service **never re-reads the singleton**: `sweep_once` reads `self._dependency_bus` only (orphan_watcher_sweep.py:216). No setter, no re-wire: the complete api.py reference set is :1895-1922 (construct/start), :3737 (comment), :4443/:4452 (shutdown).

⇒ On every boot, at step 1 the singleton is still None ⇒ `self._dependency_bus = None` **for the daemon's entire lifetime**. Every 90s tick takes the `bus is None` branch (orphan_watcher_sweep.py:219-227), emits the DEBUG line the live lane saw ("dependency_bus is not wired (legacy test fixture / pre-wiring lifespan) — relying on the startup sweep"), and cancels nothing. The comment's two named causes ("legacy test fixture / pre-wiring lifespan") are both wrong for production — in prod it is *always* pre-wiring-lifespan, permanently. The periodic lane has **never functioned in production at this commit**; its counters (`ticks` climbing, `cancelled`=0) are exactly the healthy-looking no-op the live lane observed.

**Does the fallback cover the orphan class?** The fallback is `DependencyBus.start()`'s defense-in-depth sweep — `dependency_bus.py:1753` `swept = await self._sweep_orphan_watchers()` — which runs **once per boot** (inside `init_dependency_bus`, i.e. AFTER the bus exists; this one does fire). Its predicate (`_sweep_orphan_watchers`, :2500+): cancel PENDING watchers whose `source_task_id NOT IN (SELECT id FROM task WHERE status IN ('running','pending','paused'))`, subject to the W-C 30s grace. Two coverage gaps for the observed husk class (live: instance 9c985fc7, never-dispatched, ~2.39 days, survived ≥2 boots):
- A watcher keyed on a task row **still sitting in running/pending/paused** (a stuck/husk task) is *preserved by design* — never cancelled by boot sweep or (hypothetically) by the periodic lane. Paused is intentional (pause/resume semantics); a stuck-`running` husk task row is the uncovered case.
- Between boots there is **no periodic sweep at all** (the lane that would do it is the trapped one) — watchers orphaned mid-uptime by force-cancel paths accumulate for up to an entire uptime.

**Structural lesson for the study:** a "secondary backstop" service whose dependency is captured before the dependency exists, with no re-read and no boot-time assertion — the failure is invisible at DEBUG level while its own health counters climb.

### A2 — Bash spawn reaper: EVENT-PIGGYBACKED, NO LOOP, SILENT-NO-OP PATHS, BOOT-ONLY RETENTION

**Scheduling mechanism (code truth):** there is **no standalone periodic loop** anywhere in `bash_spawn_reaper.py` (verified: module has functions only; no `while` service loop, no thread, no create_task of its own). Five lanes fire from three hosts:
1. **Instance terminalization piggyback** — `BashProcessRegistry.cleanup_instance` (tools/bash.py:~595-645) runs the RAM killpg pass then `sweep_instance` (bash.py:630) for that instance's durable rows + `_enumerated_sweep` for setsid escapees (bash_spawn_reaper.py:1379). "Terminal Tier-1/Tier-2, terminate, chart all inherit this reach with ZERO call-site changes" (bash.py docstring). So reaping cadence == instance-terminalization cadence, nothing else.
2. **Boot** — `reconcile_stale_bash_spawns` one-shot (api.py:1178-1213; 60s lane deadline) + the **only retention prune** (`prune_reaped_older_than` 30d, bash_spawn_reaper.py:1515-1527 → repositories/bash_spawn/repository.py:165 — comment: "boot-reconcile housekeeping … NOT a polling service").
3. **Shutdown** — `sweep_open_rows_for_shutdown` (manager.py:16683+).

**Failure semantics — can it die silently?** There is no long-lived executor to die. The silent paths are:
- **Kill switch OFF ⇒ complete no-op with ZERO repository access** (sweep_instance: `if not kill_switch_from_env(): return 0` — bash_spawn_reaper.py:139, :1364-1367). No log. A flipped/malformed `kill_switch_from_env` env var silently stops all three hosts' durable lanes while bash keeps spawning.
- **`repo is None` ⇒ silent no-op** (writer-unset; pre-lifespan/unit-test adjudication).
- Per-call failures are best-effort WARNING-wrapped ("a sweep failure must never break the caller", bash.py:636-641) — logged at WARNING, then forgotten; no retry (next chance = next instance terminalization / next boot).

**Durable trace when it stops:** YES, exactly one metric family + the registry itself: `ensemble_sweep_rows_total` (daemon/services/metrics_query/wiring/w2.py:373 registered, `.inc(delta, lane=…, outcome=…)` at :704). A stopped reaper shows as a frozen counter against a growing `bash_spawns` table (live: 194k rows / 70MB). No log line fires on "stopped" because stopped == no-op-on-each-invocation. **Inter-boot unbounded growth is structural**: retention prune exists only at boot; open rows close only on instance terminalization/shutdown; spawns from long-lived instances (or under a no-op'ing sweep) accumulate without bound.

### A3 — JobLockSweepService + SourcesGcService (insufficient-evidence pair, pinned from code)

**JobLockSweepService** (job_lock_sweep.py:93; loop :232-252):
- Trigger model: **fixed-interval 90s + first-tick-immediate** (sweep-then-sleep, :235-237). Config `job_lock_sweep_interval_seconds` (config.py:2709, ge=1). 🟡 its own docstring (:198-199) stale — claims sleep-first.
- Healthy trace: **rowcount log on reclaim only** (:216-220). No counters, no metrics, no last_run, no heartbeat. A healthy daemon with nothing to reclaim produces ZERO durable evidence this lane is alive — matching the live lane's insufficient-evidence verdict. (The census row's counters column is empty by design, not by omission.)
- Failure semantics: tick-level errors caught inside `sweep_once` (log + return 0 + next-tick retry, :203-214); **loop-machinery unexpected Exception ⇒ PERMANENT EXIT — personally re-verified at :241-252**: "Log loudly and exit. The next start() (if any) will re-spawn the loop." Nothing calls start() again (no supervisor; always-on wiring api.py:1965-1977). One loop-level exception = dead lane until reboot.
- Kill switch: NONE — "ALWAYS-ON, HARD POLICY" (:60-64). Concurrency: single atomic `DELETE … NOT IN (active pids)` (race-safe).

**SourcesGcService** (sources_gc_service.py:96; loop :240-262):
- Trigger model: **fixed-interval 3600s + first-tick-immediate** (`sources_gc_interval_seconds` config.py:2992; retention 14d :3005; dry_run default False :2980). NOT boot-waiting; first GC pass ~1h after boot at default cadence (first tick immediate, but the collectable predicate needs 14d-old terminal rows, so early ticks are no-ops).
- Healthy trace: dry-run ticks ALWAYS log counts + skip counters (:336); destructive ticks log when they act. No metrics family.
- Failure semantics: per-row isolation (:391); **loop-level Exception ⇒ PERMANENT EXIT** (Template-B, :256-262). Kill switch: `sources_gc_enabled` (config.py:2971, default True — the panic-off lever for a terminalizing-write lane). Collectable predicate lives at the repository layer (api.py:2112-2120 note).

---

## 3. CRITICAL DEPTH ROW — THE MAINTENANCE TIER (why 500 skips / 0 executions over 50 ticks on live)

> ⚠ **HISTORICAL AT LIVE (v0.31.22):** the skip-trap starvation chain below is code-truth at `bf4bfde6e`, but upstream v0.31.11 landed a mitigation (forced-stale execution + staleness telemetry) — see §11(a). The idle-predicate conjuncts remain the structural gate; the *permanently-due* leg is mitigated at live.

**The mechanism.** `MaintenanceService` (daemon/services/maintenance.py:102) is the daemon's GENERIC periodic housekeeping scheduler: a single asyncio loop (`_loop` :235) that sleeps 60s after boot (boot stabilization, :244), then ticks every 15 minutes (`maintenance_check_interval_minutes`, config.py:1289, const :433). Each tick runs `_run_pending_jobs` (:254): for each registered job, `_is_due` (:297-323) then a system-wide **idle gate** `_is_idle` (:326-424).

**Registered jobs (~10 on a fully-armed live nest):** checkpoint_cleanup (24h, 5 ops A–E) · vacuum_maintenance (24h) · skill_metric_scan (24h) · skill_orphan_sweep (24h) · project_skill_gc (24h) · blueprint_daily_scan (**5h**, last_run persisted in project-metadata KV of the system-default project) · malloc_trim (1h) · dependency_bus_lock_reap (1h) · anomaly_retention (24h). Registration sites: manager.py:4601-4614 (vacuum, kill-switch-gated), :4641-4657 (skill jobs, skill_evolution gate), :4667-4682 (project_skill_gc), :4700-4785 (blueprint/malloc/lock-reap/anomaly). Only **checkpoint_cleanup** and **vacuum_maintenance** write `maintenance_runs` audit rows (sections `checkpoint-cleanup`, `vacuum-maintenance`); the other 7–8 leave no DB trace.

**The idle predicate — 3 conjuncts, ALL must hold** (`_is_idle`, maintenance.py:326-424):
1. No non-deferred tasks in pending/running/paused **system-wide** — `TaskRepository.has_active_non_deferred_work(None)` (:374-383);
2. No JobItems with `admission_state` in `('queued','active')` — `list_all_pending` + `find_processing_jobs` (:391-413);
3. No active LLM requests in the request registry — `len(_request_registry) > 0` (:415-420).

On a continuously-busy live nest, at least one conjunct is virtually always false ⇒ `_run_pending_jobs` hits `if not await self._is_idle(): logger.info(f"Skipping {job.name}: system not idle"); continue` (:260-265) for every due job, every tick.

**The permanently-due trap (self-reinforcing).** `job.last_run = utcnow()` sits **after** the idle gate and only executes on success (:269). A skip `continue`s *before* that line, and `_is_due` (:297-323) returns True whenever `last_run is None` or elapsed ≥ interval. So every skipped job stays due forever: ~10 jobs × ~50 ticks ≈ 500 skip logs, 0 executions — the exact live finding. Failed executions also deliberately keep `last_run` stale (:271-274, "Don't update last_run — will retry next cycle"), so a failing job retries every tick — but the dominant cause is the idle gate. `last_run` is in-memory only (boot resets it), and there is no scheduler-level counter — the skip storm is visible ONLY in INFO logs.

**Cadence split (15-min vs 24h):** the 15-min tick is a *due-ness check*; the per-job 24h (or 5h/1h) `min_interval_hours` is the *execution interval*. `_is_due <= 0` is the per-job emergency-stop knob (interval 0 ⇒ never due; the guard is FIRST, above `last_run is None`, so a never-run disabled job never fires — :303-307 fix note: pre-fix, 0 meant due-EVERY-tick).

**Kill-switch scope correction:** `MAINTENANCE_ENDPOINTS_ENABLED` (constants.py:459; checked api.py:1091-1097) gates **only the manual HTTP API surface** — the automatic periodic tier has NO master kill switch; control is per-job via interval-0 or registration gates (`PERSISTENCE_VACUUM_ENABLED=0` skips registration entirely, manager.py:4601-4614).

**Concurrency guards:** per-section single-flight via `MaintenanceRunLock` (maintenance_run_lock.py:52; fail-fast acquire, atomic on the single event loop :78-95) + DB-side `uq_maintenance_runs_running_section` partial unique index (migration `20260927_000001_create_maintenance_runs_table.sql`); orphaned `running` rows cleared at boot by the CAS boot sweep (below).

**Boot sweep (AM-7).** `sweep_interrupted_running_runs` (maintenance_boot_sweep.py:18; `run_boot_sweep_with_retry` :40) is **awaited inside `manager.initialize()` BEFORE `_maintenance_service.start()`** (manager.py:4786-4790) — structural ordering, not the 60s sleep. CAS flips `running`→`interrupted`; 3 attempts, 1s linear backoff; on exhaustion ERROR + manual-heal SQL; never raises into boot.

**Boot CAS + maintenance_runs interplay.** `maintenance_runs` rows (started_at/section/status/interrupted) are the only durable execution record, written by 2 of ~10 jobs. A death mid-run leaves a `running` row the next boot's CAS sweep terminalizes as `interrupted` — the POK "deathclock" reading pattern (maintenance_runs.started_at + interrupted = measured death ts).

**Max-runtime gap:** `await job.execute_fn()` has **no timeout** (:267) — a wedged job blocks the whole maintenance tick loop (not the event loop — execute fns use `asyncio.to_thread` for DB), and no watchdog notices.

**Topology:** main asyncio loop (lifespan), DB via `asyncio.to_thread`; vacuum uses a dedicated NullPool autocommit engine per cycle; no dedicated thread.

---

## 4. DEPTH CARDS — REPRESENTATIVE MECHANISMS (full 12-attribute detail)

### WatchReconcileSweepService (the exemplar healthy sweep)
`daemon/services/watch_reconcile_sweep.py:91` (class), `:365-424` (`_run`). Trigger: fixed-interval **300s + first-tick IMMEDIATE** (`watch_reconcile_sweep_interval_seconds`, config.py:2844, ge=1 FAIL-FAST, env `SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS`). `sweep_once()` (:233) calls `reconcile_held_watches_for_instance(instance_id=None)` — GLOBAL, never topology-filtered; CAS claim `claim_watchers_for_job_for_instances` = exactly-once via `notify_work_watchers` (byte-identical `[JOB_EVENT]` envelopes); zombie-GC leg idempotently deletes watchers with unresolvable `job_id` + terminal liveness. Max-runtime: none. Timeout/kill: `stop()` cancel+await (:211-231). Retry: next-tick. Failure: **LOGS-AND-CONTINUES** on unexpected `Exception` (:405-409; GC leg fenced separately :294-300; `CancelledError` propagates; `_stopping` guards between ticks) — the deliberate contrast-class to JobLockSweep's Template-B. Observability: counters `fired/retired/scanned/stored_released/stored_rehydrated` + `cumulative_fired/retired/errors` (:344-355). Kill switch: NONE (always-on, api.py:1985-1991). Guards: CAS. Topology: main asyncio loop. Mints no work_id/JobItem writes/direct events.

### MissionStrandHealer (3 strand families)
`daemon/services/mission_strand_healer.py:234` (class), `:372-390` (`_run`). 300s `mission_strand_heal_interval_seconds` (config.py:2876) + immediate + event-driven heal via `child_reports._schedule_strand_heal`. Families: (i) WC root + never-dispatched ghost members; (ii) WC root whose non-root tree is all-terminal (self-referential hold, :40-55); (iii) **running-zombie root** (:60-90) — kill-switch `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED` (config.py:2910, d=True; disabled ⇒ WARN once-per-boot then DEBUG :822-827), age bound 7200s (:2922), batch cap 25 (:2945). Heal invariant CONJUNCTIVE fail-closed (:637,:843): root posture + zero bus watchers + zero messages + zero tasks + zero report injections + ALL members strand-shaped; any miss skips. Orphans terminalize ONLY via `terminate_instance(terminal_reason="orphan_retired")`; roots through the production completion chain. Concurrency: **refcounted per-root heal locks** (:471-503) serializing vs the event-driven path. Topology: main asyncio loop.

### WaitingChildrenWatchdog (hourly + wedge pass)
`daemon/services/waiting_children_watchdog.py:449`; free-function loop `run_waiting_children_watchdog_loop` :1756-1816 (task created api.py:2475-2483), run-then-sleep (immediate). 3600s `waiting_children_watchdog_interval_seconds` (config.py:2557); hang-episode threshold 3600s (:2571). Failure: per-cycle `except Exception` ERROR+continue (:1808-1815); post-tick sleep swallows CancelledError (:1816-1821). Kill switch `waiting_children_watchdog_enabled` (config.py:2547, d=True; disabled ⇒ loop returns immediately :1786-1790). Wedge pass INSIDE `run_once` (:1467+; post-stamp discipline :1484-1490), re-arm cooldown 1800s (`WEDGE_REARM_COOLDOWN_SECONDS` :194, monotonic), source `system:watchdog:wedge` (:180) — cooldown-gated, NOT hang-threshold-gated. Counters `wedge_parents_scanned/wedge_notices_enqueued` + nudge counters (:648-706). Cooldown sets + live-carrier checks (:707-830). Topology: main asyncio loop.

### Scheduler substrate (how scheduled messages actually fire)
`daemon/sources/adapters/scheduler.py` — per-source asyncio task under the adapter supervisor (sources/registry.py:838-875: restart backoff 2s→300s cap, 60s-success reset; ADAPTER_START_TIMEOUT 60s :160; per-source lock scheduling_service.py:279). Schedules: cron / interval / one-shot `run_at` (:512-563); loop waits until next trigger then `_emit_scheduled_message` (:703-845). One-shot re-arm cadence 5s with no-double-sleep (:820-842); consumption gate only on durable outcomes (direct/deferred_durable/refused_permanent — `rearmed/failed` NEVER consume, R8/DEC-9). Lateness cap: past-due one-shot beyond `one_shot_max_lateness_seconds` ⇒ SKIPPED execution row, schedule stays armed (:713-741). **Deferred/missed alarm delivery** (DEC-12): undeliverable fire defers to the instance's NEXT turn with persisted `_defer_gen/_defer_message_id/_defer_count/_defer_since` (:1166-1202), live missed-occurrence count in the body (:1261-1285). Error ⇒ 5s retry sleep; stop drains in-flight (:636-639). Observability: `schedule_executions` rows + callbacks; `deferred/deferred_count` surfaced in list items (scheduling_service.py:204-220, :680-700).

---

## 5. BOOT-ORDER MAP (service starts, `daemon/api.py` lifespan :684 → ~:4050 + `manager.initialize()`)

**Out-of-band, earliest of all:** BootSubprocessSweep — `daemon/__main__.py:567-580`, pre-config/pre-uvicorn, fail-open ×2.

**Phase 0 — `manager.initialize()` (called api.py:943, before uvicorn serves):**
1. Checkpointer init (manager.py:4432-4462) · 2. `_cleanup_stale_completions` task (:4464; loop :4870-4882; sleep-first 600s) · 3. `_cleanup_cached_instances` task (:4466; loop :7988+; sleep-first 600s) · 4. MCP warmup one-shot (:4469, if mcp_pool.enabled) · 5. Decision-module kill-switch reverify (:4472-4482) · 6. eager warm-loader (:4489-4491) · 7. **Maintenance registrations → boot sweep AWAITED (manager.py:4786-4790) → `MaintenanceService.start()` (:4792; 60s pre-first sleep)** · 8. skill-bank seeding (~:4800+, soft-fail).

**Phase 1 — lifespan body (sequential):**
9. `capture_boot_epoch` (api.py:958) · 10. MQ boot recovery (:966-971) · 11. critical-notes boot probe (:978-980) · 12. ExecutionGate lease recovery (:983-995) · 13. `manager.setup_worker_pool()` (:997 → pool_orchestrator.py): question-pack rehydrate; **StaleTaskRecovery** (thread 60s, immediate, :285-315); **StalePauseSweep** (thread 300s, immediate, :343-350); **RDRS** (ctor :363; boot sweep to_thread :476-492; start :538, thread 300s immediate); default+chat pools (:586,:650; per-task heartbeats 30s) · 14. delete_doomed task (:1069-1077; readyz-gated) · 15. bash boot reconcile (:1178-1213) · 16. RetryScheduler (:1647-1661; disabled by default) · 17. JobRecoveryService boot pass (:1689-1693) · 18. Snapshot boot sweep (:1714-1729) · 19. **drift-reconciler task** (:1793-1808; first tick +30s) · 20. eligible_pending (:1870-1889) · 21. **orphan_watcher_sweep** (:1906-1933 — constructs with the still-None bus singleton, see A1) · 22. job_lock_sweep (:1965-1977) · 23. watch_reconcile_sweep (:2014-2026) · 24. mission_strand_healer (:2067-2092) · 25. tmp_image_cleanup (:2101-2117) · 26. sources_gc (:2136-2148) · 27. plane_sync_watchdog (:2394-2401) · 28. waiting_children_watchdog task (:2475-2483) · 29. long_tool_nudge task (:2541-2550) · 30. LLM stream watchdog thread (:2586) · 31. resource monitor 2 threads (:2639) · 32. metrics listener (:2750) · 33. metrics census thread (:2783) · 34. metrics sampler thread (:2867) · 35. job_feedback_observer (:2951; event-driven) · 36. **`init_dependency_bus` (:2959 → :5332; bus.start() :5358 — warm + FIRED-unsent + one-shot orphan sweep)** · 37. system-default-project bootstrap (:2975-3040) · 38. job_processor (:3024; event-driven) · 39. `manager.start_sources()` (:3079 → manager.py:16380-16392): SourceCleanup (sleep-first 60s→6h), dispatcher, adapters · 40. readiness refresher task (:3102-3110; first tick t=0) · 41. memoir workers (:3153/:3260/:3326) · 42. upgrade-journal boot reconcile+`sweep_wake_records` (~:3510-3535) then `.start()` (:3658) + reaper worker · 43. **AutoContinue pass + sweep_orphaned_live_turns AWAITED** (wiring block :3552-3580; AutoContinue call :3575, orphan sweep call :3580; staggered) · 44. stale-pause boot pass (:3625-3645) · 45. wedge-capture watchdog thread (:3692-3700; pinned AFTER AutoContinue, W4 :3678-3684) · 46. service_reconciliation start + guaranteed boot sweep (:3787-3800) · 47. RAM guard 2 threads (:4658).

**First-tick taxonomy:** immediate (rows 13-threads, 16?, 20-28, 33, 38-41, 43-46) · delayed (drift +30s; maintenance +60s; SourceCleanup +60s; manager cleanups +600s; stream watchdog sleep-first 1s).

## 6. THE 300s CLUB (same-cadence bunching) + THUNDERING HERD

**300s club (phase-locked every 5 min):** StalePauseSweep (TH, immediate @ pool-setup) · RDRS (TH, immediate + boot sweep) · drift reconciler (+30s offset — the one deliberate breaker) · WatchReconcileSweep · MissionStrandHealer · PlaneSyncWatchdog. The two THREAD members fire first (during setup_worker_pool); the four asyncio members start seconds apart (:2014→:2067→:2394 adjacent start sites share phase) → every 300s all six re-coincide on the DB modulo drift; WC-watchdog (3600s) lands on a 300s multiple every 12th heal tick.

**Boot-time thundering-herd surface:** (a) *awaited serially on the lifespan loop* — maintenance boot sweep, MQ reset, lease recovery, question-pack rehydrate, job-lock recovery, job recovery, snapshot sweep, bus FIRED-unsent recovery, upgrade-journal reconcile + wake records, AutoContinue + orphan sweep (internally staggered ~14s/33 resumes), stale-pause boot pass, service-recon boot sweep; (b) *fire-and-forget* — bash reconcile, RDRS boot sweep (to_thread), delete-doomed (readyz-gated); (c) *first-tick-immediate recurring* — 20+ services (see §5). Boot concentration is real but serialized; the structural risk is the ~11 awaited passes extending boot wall-clock, not parallel DB stampede.

## 7. FOUND BEYOND THE SEED LEADS (11)

1. `manager._cleanup_stale_completions` — 600s sleep-first (manager.py:4464/:4870-4882).
2. `manager._cleanup_cached_instances` — 600s sleep-first TTL+LRU (manager.py:4466/:7988+).
3. `SourceCleanup` — 60s-delayed → 6h (sources/cleanup.py:48-56; manager.py:16384).
4. Per-adapter supervised restart loop — the substrate under EVERY source (sources/registry.py:838-875).
5. MCP warmup-pool health loop — 60s (mcp/warmup_pool.py:1170-1195; manager.py:4039).
6. compact_executor per-compaction heartbeat — 10s conditional (:1697-1730; :282).
7. promote_txn_hold release driver — 5s while-deferrals-exist (:422-423; :71).
8. mcps_gateway per-mission monitor — min(0.1s, timeout/10) (:494, :586-590).
9. Deferred DELETE_PHASE boot arm — +1s/+5s readyz-gated (api.py:265-275).
10. checkpoint-metrics cadence-gated publisher — 15s window, no thread (checkpoint_metrics.py:128/:153).
11. project_skill_source gc + embed-refresh — maintenance-registered + op-scoped backoff (manager.py:4667-4682; service.py:1164-1165).

## 8. DEAD / VESTIGIAL KNOBS + NON-PERIODIC NEGATIVES (config surface outliving machinery)

- **`lease_heartbeat_interval_seconds`** (config.py:3158, d=30) — ZERO consumers. ExecutionGate was collapsed to `asyncio.Lock` (execution_gate.py:28-52 documents the removed lease/heartbeat/`recover_stale_leases` design at commit `7d2836cd6`); the config field describes machinery that no longer exists. (Note: `recover_stale_leases` still runs at boot as a no-op-shaped recovery, api.py:983-995.)
- **`checkpoint_interval`** (config.py:1286, d=1) — consumer-less (config + tests only); LangGraph checkpoints per node boundary.
- **`worker_poll_interval`** (config.py:2086, d=0.5) — consumer-less; worker pools claim via notifications, no poll sleep (worker_pool.py:294-320).
- **`min_interval_seconds`** (config.py:3372) — not periodic: per-call rate limit sleep inside notify_user (:1249-1259).
- **`observer_health_check_interval_seconds`** (config.py:3251) — not a poll: `asyncio.wait_for(queue.get(), timeout=…)` idle-logging shape (job_feedback_observer.py:704-760).
- **Non-periodic negatives (leads resolved as OD/EVT):** mcp init_guard (event-driven half-open probe) · db_pool_manager (no recycle loop) · llm_failover (request-scoped) · llm_load_balancer (frozen at instance creation) · watchover_service (on-demand orchestration, zero loops) · dead_letter_service (event-routed) · question_pause_set/question_manager (no timeout sweeper at this commit) · prompt_refresh (on-demand) · secret_persistence_scrub (library) · embedding services (write-path + explicit backfill, no loops) · bash_spawn_reaper (see A2) · malloc_tuner (hosted by maintenance) · mission_live_guard (on-demand) · timeout_monitor (one-shot per task).

## 9. GAPS / UNCERTAINTIES

- 🟡 `file_change_monitor.DEFAULT_POLL_INTERVAL_S` exact numeric value not captured (name verified :79-83).
- 🟡 RDRS lane count: docstring says seven (:22,:57) but wiring/log carry eight lane flags (wc_orphan_detector = observability-only 8th) — reported both.
- 🟡 JobLockSweep `sweep_once` docstring (:198-199) contradicts its own loop (sleep-first claim vs sweep-first code :235-237).
- ⚠ Version skew: this census is CODE at `bf4bfde6e` (v0.30.9-era); live runs v0.31.22. Specific deltas flagged: ram-guard poll = 200ms in this tree (commission said "~1s" — either newer-than-this-tree, live env override, or L1-lane change in flight); metrics census DORMANT-by-flag on live (census_enabled=false) while code default is ON-shaped.
- 🟡 Worker C classified `graph.py:9206` (loop-repair breather sleep) from grep context only, per the no-deep-read constraint on the L1-edited file.
- Not covered (deliberately): FE's 16 pollers (sibling lane); event-source machinery (sibling census-events); live-execution proof (maintenancer lane).

## 10. TOPOLOGY SUMMARY (the census's structural finding)

Four persistent shapes: **(a) daemon threads off the event loop** for starved-loop survival — ram guard (×2, 200ms), wedge capture (1Hz), metrics census (15s) + sampler (10s), resource sampler (30s) + flusher, stream watchdog (1s), log_sumup timer, TaskHeartbeat (×N), TimeoutMonitor (×task) — 10+ threads minimum at steady state; **(b) lifespan asyncio tasks** for DB-touching sweeps — readiness (10s), plane watchdog, tmp-image, memoir ×3, drift reconciler, the 90s/300s sweep family; **(c) per-entity conditional loops** — scheduler sources under the adapter supervisor, per-worker heartbeats, per-server vscode health, per-mission gateway monitor; **(d) cadence-gated piggybacks with no thread of their own** — anomaly pass on the flusher, checkpoint-metrics publish on increments, queue_freshness + checkpoint sentinel on the readiness tick, bash reaper on instance terminalization.

**Engine-relevant defect classes this census surfaces (evidence-backed):** (1) idle-predicate starvation + success-gated last_run (maintenance tier — §3); (2) construction-order dependency capture with no re-read (OrphanWatcher — A1); (3) Template-B PERMANENT-EXIT loops with no supervisor and (for JobLock) no healthy-trace (A3 + rows 1/38/58/27); (4) silent no-op paths behind env switches (bash reaper kill-switch, metrics census flag); (5) 300s phase-locking across six services; (6) boot-serialization wall-clock from ~11 awaited passes; (7) config knobs outliving removed machinery (§8); (8) audit-trail asymmetry — only 2 of ~10 maintenance jobs write durable rows; several always-on sweeps log only when they act (healthy = invisible).

---

## 11. v0.31.22 CORRECTIONS (2026-11-21, per review)

The census body above remains CODE TRUTH at `bf4bfde6e`. This section carries the drift between that tree and the LIVE daemon (v0.31.22), as supplied by review (the frozen-binary release layout ships no `daemon/` source tree, so these anchors are carried per review, not re-derived from this checkout).

**(a) Maintenance-tier starvation — LANDED MITIGATION at live (mark §3 historical).** The v0.31.11 composite fix (the wi-4d1fa093 maintenance-starvation lane, staged v0.31.11) lands a forced-stale escape from the permanently-due trap: `maintenance.py:501` `forced = gated and self._is_stale(job)` — a job gated by the idle predicate for longer than the staleness bound is executed FORCED despite the gate; 24h `max_staleness` (`maintenance.py:210`); `job_metrics` telemetry adds `last_success_ts` / `consecutive_skips` so skip-storms are durably observable instead of log-only. Net at live: §3's "500 skips / 0 executions" trap shape is historical — gated jobs now fire at least once per staleness window, and skip streaks leave telemetry. The 3-conjunct idle predicate itself is unchanged (it still defers execution on a busy nest; only the forever-deferred leg is closed).

**(b) TERMINATED-only child-keyed fire — S1 landed+wired at v0.31.22.** The COMPLETED-path defect recorded in the critical notes (a nudge-armed watcher whose target is not the child's structural parent could never fire on COMPLETED — only TERMINATED fired it) is closed at live by `fire_for_terminal_child`: `daemon/services/dependency_bus.py:1921` (the fire), wired at `child_reports.py:6444` / `:6643` / `:6945` and `error_reporting.py:693`. Relevant to the event-machinery sibling census; recorded here so the code-truth file does not understate live capability.
