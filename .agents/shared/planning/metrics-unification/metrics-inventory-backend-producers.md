# METRICS INVENTORY — BACKEND PRODUCERS (agents-ensemble)

**⚠ FILE NOT WRITTEN BY ME.** As the read-only wanderer I do not perform file writes by discipline — the commission's fallback applies: **the complete report content is this message**. Leader: stage it at `/home/nea/ensemble-src/.agents/shared/planning/metrics-unification/metrics-inventory-backend-producers.md` (dir already exists beside the sibling's `metrics-inventory-surfaces-fe-deps.md`).

**Content ref (all citations):** `origin/latest = 62627265f52576200f7917d37fbe4deeab897bae` (2026-11-01 19:53:17 +0700, "! New version 0.28.4") — newer than the v0.28.3 tag `a84af3ce1`; `origin/latest` used throughout, read exclusively via the git object DB (`git grep`/`git show`/`ls-tree`). Working tree (v0.27.8-era) never trusted for tracked files. Method: 3 self-verification passes + 5 delegated worker investigations (resource, loop/pool, registry, core-family, usage-misc), all adjudicated 🟢 on evidence.

**Executive digest:** (1) A **canonical stdlib-only Prometheus-exposition registry already exists and is live code** (`checkpoint_metrics.py`, 18 families, in-RAM, pull-only, 2 surfaces, default OFF) — breadcrumb 12's "explicit absence" is wrong as stated: no `prometheus_client`/OTel/statsd/**psutil** deps (uv.lock clean), but exposition is hand-rendered Prometheus classic text. (2) The in-flight central-metrics program is **already planned and partially shipped**: committed `.agents/shared/planning/metrics-collection/` (metric-catalog.md) specifies 27 families F0.1–F22+F7b/F7c, 582/2000 series budget, and the doctrine *"Prometheus authoritative for alerting; resource_monitor PG tier authoritative for per-instance views; /metrics is a view over the same services."* (3) The resource monitor is the only subsystem with **disciplined tiered retention**; everything usage-shaped is durable-but-unbounded; LLM token usage is **captured then dropped** (the single largest hole). (4) Scatter is real but structured: three metric conventions coexist (`ensemble_*` registry / per-module DB counters / log-lines), and several concepts are measured 2–3 times in different shapes.

---

## S1 — PRODUCERS

### 1. Metrics-registry lane (WS-B "metrics-collection", merged `bf673fdbe`, rode v0.27.13; 30 commits, +3224/−250, 9 daemon files)

| Producer | Measures | Cadence/trigger | Destination |
|---|---|---|---|
| `daemon/checkpoint_metrics.py` — the registry (1573L) | 18 registered families: checkpoint saver-op latency (`ensemble_checkpoint_op_seconds` hist, `op∈{aget,aput,adelete,alist,other}`), `…_list_total`, READ family (`…_read_ops/rows/bytes/size_bucket_hits/measurement_errors_total`, `…_read_size_bytes` hist, `…_leak_signal_total`), cache hit/miss, registry bookkeeping (`registry_measurements`, `cap_rejected_total`, `invalid_label_total`, `vocabulary_version`) + 3 listener self-obs (`metrics_up`, `scrape_requests_total`, `exposition_seconds`) | increment at each op; copy-on-publish snapshot ≤15s (`DEFAULT_PUBLISHER_CADENCE_SECONDS=15.0` :128) | in-RAM only (module `_METRICS` :374-380, process lifetime) → rendered by 2 consumers |
| Increment call-sites | `persistence.py:797` (saver ops via `checkpoint_perf.log_saver_op`), `checkpoint_adapter.py:444` (reads via retry proxy), `:741/:795/:823` (cache), `:331` (read-measure errors, fail-open), `readiness.py:1153` (leak predicate per tick), `metrics_listener._record_scrape` | per op / per tick | registry |
| `daemon/services/metrics_listener.py` (593L) | scrape serving | per request | `127.0.0.1:9798` `GET /metrics`, own daemon thread (ThreadingHTTPServer), origin-guard 403, **never 503** (MA-15), empty-body-when-off, `text/plain; version=0.0.4` (MA-16) |
| `daemon/routers/metrics.py` (141L) | same registry, in-app | per request | `GET /api/metrics`, scope `maintenance:read`, **503 `AUTH_NOT_CONFIGURED` fail-closed when auth off** (D3), mounted `api.py:5278` |
| `daemon/services/metrics_gate.py` (534L) | kill-switch + managed keys | per request (env read, no cache) | `ENSEMBLE_METRICS_ENABLED` (explicit wins; every non-`{1,true,yes}` explicit value = OFF; unset ⇒ managed key `limits.metrics_enabled`); live keys `limits.metrics_enabled / metrics_scrape_interval_hint_s / metrics_cardinality_cap` via dedicated refresher thread that **never blocks on a pooled checkout** (F1, scrape_timeout 10s rationale) |
| `daemon/services/metrics_census.py` (502L) | PG idle-in-txn backends by site (6-site closed set + `other`) | dedicated thread, 15s floor–300s cap; dedicated **non-pooled** conn `application_name='ensemble-metrics-census'`, stmt-timeout 5s | in-RAM `CensusSnapshot` (`state_counts`, `idle_in_txn_by_site`, `idle_in_txn_max_age_by_site`, `last_success_epoch`) — **NO consumer at this ref** (`ensemble_pg_*` = planned P1a) |
| `daemon/services/report_integrity_metrics.py` (105L) | terminal-report junk shape (NR-3) | per terminal fetch | module int + ONE structured INFO log line; `attach_sink(dict)` seam exists, **no sink attached**; name `report_integrity_junk_report_total` (`constants.py:1588`) predates/violates the `^ensemble_` canonical rule |
| Knobs (`config.py:786-860`) | `metrics_enabled=**False**`, `metrics_listener_port=9798`, `metrics_scrape_interval_hint_s=15` (5-300, live), `metrics_census_interval_s=15` (15-300), `metrics_sampler_interval_s=10` (declared; **no sampler module exists** — P1a), `metrics_textfile_dir=$INSTALL_DIR/data/metrics` (declared; **no writer** — P2), `metrics_cardinality_cap=2000` (live) | — | config + `settings_service.py:495-560` |

Push/pull: **pull-only**; no pushgateway/exporter. Registry safety design: per-metric locks, non-blocking render (50ms/family, 250ms total budget, stale-snapshot fallback), three-tier label enforcement, hard cardinality cap, frozen label vocabularies (instance status ×10, sweep lane ×13, MCP roster ×5, tool categories from committed artifact `.agents/shared/metrics/tool-categories.json`).

### 2. Resource-monitor family (WS-A; `daemon/services/resource_monitor/`)

| Producer | Measures | Cadence | Destination |
|---|---|---|---|
| `sampler.py` (1 daemon thread) | daemon-self `/proc/self/{stat,statm,status}` RSS/VmHWM/VmSize/threads/fds/cpu + cgroup v2 `memory.{current,stat,peak}`/`cpu.stat` + PSS (1/10 ticks) + per-registered-child `/proc/<pid>` (PID-recycle defense via starttime) + per-instance RAM attribution (`charged_bytes` via always-on charge seams `note_message_append/replace/checkpoint_read`; `deep_bytes` via 1-instance/tick deep scan; `in_flight_tasks`; `all_state_messages_len`) | tick 30s (clamp [15,60]); ladder deep/full/reduced/minimal with demote-on-overrun; self-cost budget 1.0% CPU / EMA guard | RAM `TierRing` only — **"Sampler NEVER touches the DB"** |
| `flusher.py` (1 daemon thread) | drains rings + pid history + instance ring; rollups; retention sweeps | window 60s, ONE `engine.begin()` txn | PG: T1 raw+minute+hour, T3 `resource_pid_map`, T2 `resource_instance_sample` |
| `resource_anomaly.py` | MAD robust-z + one-sided CUSUM over T1/T2 (5-min buckets) | piggybacks flusher thread, gate 600s [300,900] | **RAM only, NO table**; recomputed from DB on cold boot; served `/api/resources/anomalies` |
| `resource_traceback.py` | read-side JOIN resource_* × task/message_metadata/event/instances/service_tracking ("why was RAM high at T") | on request | nothing persisted |
| Readers | `/api/resources/{live,history,self-cost,traceback,anomalies(+/{id}/evidence),control-capabilities}` (`routers/resources.py`) | — | REST only |
| Knobs | 16 × `RESOURCE_MONITOR_*` (`enabled=True` kill-switch, `sample_seconds=30`, retentions, `instance_cap=50` (hard 64 + `__overflow__` sentinel), `self_cpu_budget_pct=1.0`, `anomaly_*`, `profiling_enabled=False`, `flush_batch_seconds=60`) — restart-required; trap: literal `config.yaml resource_monitor:` block kills the env tier | — | — |

**T1–T4 are the four TABLES, not flush tiers** (corrects the briefing; storage tiering raw/minute/hour lives *inside* T1 via `interval_s`). Boolean semantics: `resource_instance_sample.estimated` = NOT NULL default **true** — an honesty label ("per-instance attribution partial by physics"), gates nothing; `resource_limit_policy.enabled` default **false** + `user_gated` default **true** — table ships EMPTY, observe-only, **no enforcement consumer in v1**. 2026-10-30 10-11GB ballooners: recorded in T1 cgroup columns (`cg_current_kb`/`cg_peak_kb` cover the whole unit cgroup) + daemon-self columns; OOM kill events themselves live only in the host systemd journal.

### 3. Wedge-capture subsystem (`daemon/services/wedge_capture/`, 10 modules + `repositories/wedge_captures/` + `routers/wedge_capture.py`)

- **Watchdog**: dedicated daemon thread, tick 1.0s, samples `/proc` CPU+memory+pool stats from OUTSIDE the loop; kill-switch `ENSEMBLE_WEDGE_CAPTURE_ENABLED`; 15 env knobs; capture deadline 20s; constructed `api.py:3048-3064`.
- **Triggers** (`triggers.py`, defaults `config.py:98-109`): `sustained_cpu` ≥95% of one core / 30s; `sustained_loop_lag` ≥5.0s / 15s; `thread_concentration` ≥85% of process CPU + ≥5% absolute floor / 30s; `manual` (2/min cap). Per-reason cooldown 300s. Sustain = trailing wall-time run.
- **Capture payload**: 8 fixed files per capture dir (`manifest.json`, `stacks.txt`, `threads.csv`, `lag.json` — 300-sample histogram p50/p95/max/overflow/`loop_unresponsive_s`, `metrics.json`, `db.json`, `readyz.json`, `checkpoint_perf_tail.json`) + PG row = metadata pointer (trigger_reason, boot_epoch, release_version, `metrics_snapshot` JSONB, file_count/total_bytes). File-first, row-second.
- **Retention**: directory-authoritative prune 20 captures / 168h / 512MB, runs before write.
- **R-10 census defect**: located `db_capture.py::_read_store_census` — **already REPAIRED** at this ref (`pg_class LEFT JOIN pg_stat_user_tables` NULL-safe, comment :374-390); "born-broken" was the pre-fix state only.
- **Surfaces**: `GET /api/wedge-captures` + `/status` (lag probe health: `last_probe_age_s`, `lag_probe_state`, `lag_never_fired`, `lag_stale`).
- **Loop lag is single-sourced** in `lag_probe.py` (`call_soon_threadsafe`, ≤1 outstanding, growing-sample semantics so a starved loop deterministically accumulates); readiness and the registry carry **no** loop-lag series (F1 = planned P1a over this probe).

### 4. Readiness internals (`daemon/services/readiness.py` + `api.py`)

- Background refresher thread, **10s** (`services.readiness_refresh_interval_seconds`) — the only DB toucher; `/readyz` handler = O(1) read of replaced in-memory composite. **Nothing persisted** (except MCP transitions via init_guard journal).
- Components (4 fixed): `database` (SELECT 1, 0.5s budget), `queue_freshness` (MAX age of `task.last_heartbeat_at` over RUNNING vs 120s threshold; 4-word age-provenance vocabulary), `services`, `checkpoint_saver` (write-once sentinel read through the real saver path every tick, 1.0s fail-closed — where the 5.4-8.1s live timeouts surfaced).
- `detail` de-facto interface keys: `grace{grace_active, horizon 120s (READINESS_GRACE_HORIZON_S), never re-armed}`; `leak_signal{detected, checkedout_rising, streak≥3}` — oracle = psycopg checkpoint pool, advisory = manager pool; leak is NEVER grace-eligible (fail-closed); `pool_state{sqlalchemy,psychic}` (in-memory pool reads, no DB round-trip); `resume_hold`; `burst_signals`; `mcp{per-server}` + `mcp_buffered_transitions` (advisory only); `skill_sources`; `queue_max_age_seconds[_reason]`; `component_classes`; `inflight_turns`.
- F4-A probe admission pre-check: derived capacity 25 (`MANAGER_POOL_CONFIGURED_CAPACITY`, `readiness.py:195` — this is the "25"; **db_offload itself has 6 workers**, two different quantities).

### 5. DB-pool & executor instrumentation

- **Pool knobs**: manager engine `pool_pre_ping=True`, `reset_on_return="rollback"`, `recycle=3600`, `timeout=30` (`factory.py:436-452`); repair 2+3 pool (`ens_db_tools.py:586-625`); checkpoint psycopg pool `min 1 / max 10 / timeout 5.0s / max_lifetime 3600` (`persistence.py:346-363`).
- **`application_name` per pool — observable in `pg_stat_activity`**: `ensemble-manager` (`factory.py:390`), `ensemble-repair` (`ens_db_tools.py:193/:650`), `ensemble-checkpoint-adapter` (`persistence.py:65/:462`), plus diagnostic `ensemble-metrics-census` and `wedge-capture`.
- **Measured guard (F7)**: TEST-pack invariant only (occupancy-returns-to-baseline + `xact_age_s` budgets 0.05s/0.25s, `constants.py:390/:394`); runtime counterpart = the census sampler.
- **D-47 idle-in-txn backstop**: `MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS=60000` defined-but-disabled (`constants.py:314-320`, boot logs DISABLED; one-flag enablement in `factory.py`).
- **db_offload executor** (`services/db_offload.py`): 6 workers (`constants.py:1776`), NO-RESERVE gate invariant `permits+inflight+in_transit==W`, counters exposed as in-module properties + backpressure WARNING — **log-only, zero external consumers**; `DB_OFFLOAD_QUEUE_WARN_DEPTH=4` declared with no consumer.
- **checkpoint_perf**: slow-saver ledger (cut 5000ms, deque 128 → capture tail), `[CheckpointPerf]` windowed log lines (180s window, `CHECKPOINT_PERF_LOGS` gate), and the registry histograms (F7/F7b).

### 6. Core systems family (the addendum — module-local metrics first-class)

**6a. Mission.** Three substrates coexist: (i) `mission_records` — epoch rows (anchor, `epoch_seq`, `founding_source`, status open/settled/closed/superseded, provenance); mint via guarded upsert `ON CONFLICT … DO NOTHING RETURNING` loser-signal (`repository.py:564-590`), narrow `MissionEpochConflictError` caught at exactly one site (`mission_record_service.py:557`) — the known ~4×/boot UniqueViolation race is now idempotent; **mint outcomes are LOG-ONLY** (wrapper warnings :1334+; no counter anywhere). Mode ladder `ENSEMBLE_MISSION_RECORDS=off|dry|enforce`, ship default **dry**. (ii) `mission_commissions` — `lane` label, state machine outstanding→satisfied/abandoned/superseded (frozen `abandon_reason` vocabulary), **`resolved_at` is the ONLY timing observable** (no durations computed/exposed anywhere); single guarded UPDATE `transition_commission_state` (`repository.py:724`); lane-7 RDRS sweep (300s loop, TTL 6h) computes per-pass disposition counters (`abandoned_worker_terminal/ttl/worker_missing`, `satisfied_on_evidence`, `skipped_live/…`) — **in-memory per pass, served only in `POST /api/recovery/report-delivery` response**. (iii) `MissionResolver` FE surface (`GET /api/missions`) — an *Instance-row projection* with **zero joins** to the records tables (two mission systems by design; "join later" per migration docstring); `mission_id` semantics differ between them.

**6b. Task timing.** `task.started_at` (atomic claim stamp), `completed_at`, `last_heartbeat_at` — durable; **durations and queue-wait are derivable, never stored**; single batched timing query `TaskRepository.get_timing_by_work_ids` behind `/api/jobs` + `/api/work` (`work_resolver.py:1451-1460`); no histograms anywhere. Heartbeat written by per-worker `TaskHeartbeat` threads, 30s.

**6c. Worker pool** (`services/worker_pool.py`). `get_stats()` literal keys: `num_workers, started, stopped, is_running, notifications_sent, empty_claim_attempts, workers_woken_by_timeout, claims_skipped_due_to_busy_instance, wakeup_efficiency, workers[], pool_pending_tasks`; per-worker: `worker_id, tasks_claimed, tasks_completed, tasks_failed, is_alive`. **In-memory ints under `_stats_lock`, zero storage**; **nine inline increment sites** (:312, :555, :829, :852, :874, :899, :1077, :1089, +:1403/:1416/:1447 pool-level). Consumers: `instance_read.py:2094` (fleet census), `routers/resources.py:948`. Sizing 5+2 (`WORKER_POOL_SIZE`/`CHAT_WORKER_POOL_SIZE`, managed `limits.worker_pool_size`, restart-applied); threads named `worker-<lane>-<id>` (thread-CPU family groundwork).

**6d. Job/queue stats.** `MessageQueueRepository.get_stats(instance_id)` returns EXACTLY `{"pending_count", "processing_count", "oldest_message_age_seconds"}` — computed in the repository (3 SELECTs); **`pending_count` counts READY rows** (`repository.py:1641-1647`, confirms the v0.27.11 disclosure). Send-gate wrapper `manager.get_queue_stats` zeroes for terminal instances (two-truths hazard). Admission tallies: `admission_state` ∈ QUEUED/ACTIVE/DONE/DEAD durable on `job_queue_items`; computed-on-read via repo methods (`JobRepository.count_active_jobs_*`) **plus duplicated as inline SQL in the tool/router layers** (`instance_read.py:2046-2061`, `resources.py:930-936`). DLQ: `dead_letter_items` durable snapshots (labels project/queue/agent/reason; retention-pruned); `dead_jobs_total` (Σ admission dead) deliberately ≠ `dlq_depth`, `dlq_note` documents it; **no replay-outcome counters**. Badge/list enrichment (`/api/jobs`, `/api/work`, `/api/instances` 8s/60s polls): pure list projection — **no per-request latency/counters exist** (only signal = access-log hiding).

**6e. service_tracking** (20260915): pid→instance subprocess spine (name/command/pid/starttime/status/started_by_*/exit_code); identity + resource traceback; **no stats use at this ref**.

### 7. Usage families

- **LLM tokens**: `usage_metadata` captured on every streamed call (`graph.py:3856-3920`, `stream_usage=True` injected `:3977-4014`) then **dropped — zero consumers ref-wide**; plugin lane (`opendesign/generate.py:976-1000/:1328-1344`) keeps usage plugin-local; failover observability = WARNING log lines only (`[LLM-HA]`, `failover_summary()`); compaction `tokens_before/saved` are tiktoken **estimates**, not provider usage. Catalog F14 names the planned hook (terminal-usage branch, per-call dedupe — "terminal-chunk only, never per-chunk").
- **snapshot_usage_counters** (20260925_233000): `scope/key/value` cumulative counters (`capture:agent:<id>`, `spawn:snapshot:<id>`), single-statement upsert, fail-soft; writers `snapshot_tools.py:571/:636/:1043` (warm-path only); readers `SnapshotMetricsService.surface()` → `GET /api/snapshots/metrics` (+ deprecated proxy); **no retention**; self-contained service.
- **Skill usage**: `skill_usage_records` (+20260715 A/B cols, +20260721 feedback cols) + fork `project_skill_usage_records` (20261031, no FK, `pf-` ids); writer = Tier-0 `SkillMetricsService.record_task_completion` from job-queue completion hooks (`job_queue_service.py:3617/:3665`, `task_processor.py:837`) + `record_feedback` (tool `skill_tools.py:1491`, FE `routers/skills.py:1316`); readers `GET /api/skills/{id}/metrics`, `/usage-records`, A/B comparison; denormalized counters on `skills` rows; **no retention**; **excluded from /metrics by decision m5** (own surface, double-count guard).
- **Instance census**: `fleet_capacity_summary`/`instance_fleet_summary` tools — `count_fleet_by_status_and_agent` one grouped query; buckets = raw statuses; liveness = canonical 7-vocabulary with the **F2 idle→processing fold** (idle is a status bucket only); `worker_pool{size,busy,idle,saturated}` in-memory (leaks `_workers` privates); `dlq_depth`+`dead_jobs_total`+`dlq_note`; `audit_pending{count,oldest_age_seconds}`; **computed-and-dropped per tool call — tool JSON only, never persisted**.
- **Heartbeat/staleness**: durable `task.last_heartbeat_at` (30s beats, indexed, NULLed on terminal); verdicts computed ad hoc by ≥4 consumers with divergent thresholds (tool 30min, `stale_task_recovery_threshold_minutes`, readiness 120s, report-injection `heartbeat_threshold`); instance uptime computed **nowhere** durably (derived per census row).
- **Sources resilience**: `CircuitBreaker` (state enum, failure_count, threshold 5 / 60s recovery) + `TokenBucketLimiter` (`available_tokens` "for monitoring") — in-memory per adapter, `time.monotonic()` stamps, **zero exposure, zero consumers outside `daemon/sources/`**; denials uncounted.

### 8. Bash children & process observation

- `bash_spawn_registry` (20261031) = **kill-path tombstone, NOT a metrics producer** (pid/pgid/start_time/tracking_id/boot_id/reap audit; 30d retention; boot-reconcile one-shot + 5 kill lanes). The metrics leg of the same spawn seam is T3 `resource_pid_map`: `peak_rss_kb` (VmHWM at exit, `bash.py:373-379`) + `cpu_user_usec`, kinds `bash|chart_render|mcp_child|service|proc`, 7d post-exit. One spawn event feeds both registries.
- `/proc`-based throughout, **zero psutil** (only a docstring mention); boot orphan sweep (`boot_subprocess_sweep.py`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`) is **kill-only** + one log line (`term/kill/guard_skips/grace`).

---

## S2 — STORAGE

**PG tables (metrics/telemetry/audit-shaped):**

| Table | One-line schema/purpose | Retention |
|---|---|---|
| `resource_daemon_bucket` (T1) | per-tick daemon+cgroup buckets; **3 stacked tiers via `interval_s`** (raw<60 / 60 / 3600) | raw 48h, minute 14d, hour 180d (floors 24h/7d/90d) |
| `resource_instance_sample` (T2) | per-instance attribution (charged/deep bytes, in_flight_tasks, child aggregates, `estimated=true`) cap 50+overflow | swept at raw cutoff |
| `resource_pid_map` (T3) | child registry incl. `peak_rss_kb`, `cpu_user_usec` | 7d post-exit |
| `resource_limit_policy` (T4) | control seam — **ships EMPTY**, observe-only (`enabled=false`, `user_gated=true`) | n/a |
| `wedge_captures` | capture metadata rows (JSONB metrics_snapshot) + 8-file bundles on disk | dir-authoritative 20/168h/512MB |
| `snapshot_usage_counters` | cumulative scope/key usage ints | **none** |
| `skill_usage_records` + `project_skill_usage_records` | per-event usage rows (skill/instance/agent/project, A/B, feedback) | **none** |
| `dead_letter_items` | DLQ snapshots (project/queue/agent/reason/metadata JSON) | retention-pruned |
| `drift_events` | plugin-snapshot divergence ledger | none found |
| `maintenance_runs` | checkpoint-cleanup run ledger w/ JSON summaries (status/durations in `summary_json`) | none found |
| `instance_ops_audit`, `notify_audit`, `secrets_audit`, `mcp_gateway_audit` | audit ledgers (breadcrumb-known; census surfaces `audit_pending` counts from instance_ops) | per-lane |
| `bash_spawn_registry` | kill-path tombstones | 30d (boot-reconcile prune) |
| `service_tracking` | subprocess spine | unbounded |
| Metrics-adjacent domain: `task` (timing/heartbeat), `job_queue_items` (admission_state), `mission_records`/`mission_commissions`, `event` (8 lifecycle kinds), `message_metadata`, `stored_watches`, `prompt_versions`/`prompt_template_baselines` | durable domain state from which metrics derive | n/a |

**In-memory (process-lifetime):** checkpoint_metrics registry (18 families) · readiness composite + GraceTracker + leak streak · CensusSnapshot (no consumer!) · worker-pool stat ints · sampler/flusher self-cost counters + anomaly store + charge/TierRing buffers · db_offload gate counters · circuit breakers + token buckets · lag histogram (also persisted per-capture in `lag.json`).

**Log-line-only:** NR-3 junk line · mission mint/conflict outcomes · `[LLM-HA]` failover warnings · breaker transitions · boot-sweep term/kill line · `[CheckpointPerf]` windows. **File/jsonl-shaped:** wedge bundles · upgrade per-run two-file log sink + journal `in_flight.last_heartbeat` epoch (`scripts/upgrade/lib.sh:1784/:1844-1905`) · textfile dir (declared, no writer).

---

## S5 — GAPS & SCATTER (backend)

1. **Three metric conventions coexist**: canonical `ensemble_*` registry (18 families) · per-module DB counters (snapshot `scope:key` strings, skill snake_case rows) · pre-registry orphans (`report_integrity_junk_report_total` violates the `^ensemble_` rule; sink seam unattached).
2. **Same concept, different shape, 2–3×**: pool state (readiness `pool_state` samples vs census idle-in-txn vs wedge `db.json`) · admission tallies (repo methods vs inline SQL duplicated in tool+router layers) · dead counts (`dead_jobs_total` vs `dlq_depth`, documented) · staleness verdicts (4 consumers, divergent thresholds, minutes-vs-seconds) · loop lag single-sourced but surfaced 3 ways (capture trigger, `lag.json`, `/status` — good, not scattered).
3. **Naming drift**: raw 10-value instance statuses vs 7-value canonical liveness (idle→processing fold) vs lowercase enums vs stringly scope prefixes; `worker_id`/thread-name encodes lane by suffix, not a label.
4. **Retention is bimodal**: resource monitor = the only disciplined tiering; everything usage-shaped = durable-but-unbounded (snapshot, skill, drift, maintenance_runs, service_tracking).
5. **Mission**: two coexisting systems (resolver projection vs records tables) with different `mission_id` semantics; **no F-family in the catalog**; mint failures log-only.
6. **LLM tokens**: capture-then-drop — biggest single hole; no per-model/per-agent/per-project anything.
7. **Produced-but-unexported**: CensusSnapshot (zero consumers), db_offload counters (log-only), worker-pool stats (in-memory), breakers/buckets (invisible).
8. **Timestamp frames**: naive-UTC discipline (readiness/get_stats) vs `time.monotonic()` (breakers — not epoch-comparable) vs ISO TEXT vs journal epoch ints.
9. **Event-loop coupling** (adapter-relevant): sampler/flusher/watchdog/census/listener = dedicated threads; readiness refresher on-loop (10s); badge/list reads off-loop since v0.27.14 — any hook on those paths must not reintroduce on-loop work.

---

## S6 — SEAM ASSESSMENT (per phasing clarification: central system first, modules onboard later)

**S6.0 — The registration contract largely already exists on paper.** `metric-catalog.md` = 27 families (F0.1–F0.4, F1–F21, F7b, F7c), each declaring name/type/labels/**source-anchor**/phase/owner/degradation + frozen vocabularies + cardinality budget (582 daemon-origin / ≤2000; ≈651 grand incl. exporters). **Shipped at this ref**: F0.1, F0.2, F0.4 (P0) + F7b + F7c + partial F7 (= the 18 registered objects). **Planned with named seams**: F1 loop-lag (`wedge_capture/lag_probe.py`), F2 thread-CPU (`wedge_capture/sampler.py` + worker thread names), F3 wedge counters (`triggers.py`), F4/F5 pool (readiness samples), F6 `ensemble_pg_*` (census snapshot), F7 completion (op_bytes/slow_ops), F8 store sizes (`maintenance.py:531`), F9 readiness (`readiness.py:64-79`), F10 queue depth (admission_state), F11 worker pool/heartbeat/claim-latency (`worker_pool.py`, `task/models.py:284`), F12 sweeps (13-lane vocabulary already frozen), F13 instance counts, F14 LLM tokens (terminal-usage branch, per-call dedupe), F15/F16 LLM (`llm_error_classifier.py`, `llm_stream_watchdog.py`), F17/F18 MCP (`init_guard`, `mcps_gateway`), F19 compaction, F20 turns/tools (`message_tap.py:234`), F21 journal twin (`upgrade_journal.py`), F22 textfile/exporters (out-of-process sampler). **Catalog-vs-inventory deltas**: mission = no family; sources breakers/buckets = no family; badge/request latency = no family; snapshot metrics = open item H-7; skill metrics = excluded by m5. Doctrine (decision 4) pre-answers the Prometheus-vs-PG split.

**S6.1 — Onboarding tier ranking:**

- **TIER-1 (durable + stable shape, read-only adapter suffices):** resource monitor T1–T4 (the model citizen); `task` rows (durations/queue-wait derivable); `job_queue_items` admission tallies; `dead_letter_items`; `mission_records` census; `mission_commissions` (latencies derivable from `created_at`/`resolved_at`); `snapshot_usage_counters`; skill-usage tables (dual-fork hazard); `service_tracking`; heartbeat columns; `instance_execution_leases`.
- **TIER-2 (exists, wrong shape — rework at onboarding):** worker-pool `get_stats()` (in-memory, 9 inline sites, aggregates-only); instance census aggregate (computed-and-dropped per tool call; tool-JSON shape; in-memory pool half); readiness internals (ephemeral per-probe, but the `detail` dict is a stable de-facto interface; F9 planned); metrics_census snapshot (exists, no consumer, families unregistered = F6); mission resolver projection (dual mission_id); db-pool state gauges (in-memory reads, F4/F5); heartbeat verdicts (fragmented thresholds); wedge capture counters (F3).
- **TIER-3 (name-only/absent — new instrumentation):** LLM tokens (F14 — hook site already named); LLM request/latency (F15) + stream stalls (F16); badge/request latency (uncatalogued); mission mint-failure outcomes (log-only, 6 wrapper sites); DLQ replay outcomes; sources circuit-breakers/token buckets (uncatalogued, zero exposure); loop-lag exposition family (probe exists, no registry series, F1); compaction (F19); turn/tool-call seam (F20); journal twin (F21).

**S6.2 — Registration inputs (observed facts, not design):** every TIER-1 producer has a natural PULL boundary already used by ≥1 consumer (`WorkerPool.get_stats`, `MessageQueueRepository.get_stats`, `TaskRepository.count_*/get_timing_by_work_ids`, `MissionRecordRepository.count_*`, `DeadLetterService.count_dlq`, `JobRepository.count_active_jobs_*`, resource REST, `SnapshotMetricsService.surface()`, `SkillMetricsService.get_*`). PUSH would be needed only at: the 9 worker-pool increment sites, 6 mission fail-open wrapper `except`s, lane-7 disposition fields, `replay_from_dlq`, route/middleware seam for request latency, `graph.py:3915-3916` usage attach, breaker `record_success/record_failure`. Durability split: durable PG rows (TIER-1 list) vs process-local (registry, pools, census, breakers — all reset-on-restart; registry counters are `rate()`-safe resets by design). Grains: 30s samples / 60s flush / 10s readiness / 15s census / 1s watchdog / per-event (usage, mints, DLQ). Hazards: dual-truth pairs (pending_count wrapper vs repository; dead_jobs_total vs dlq_depth; two mission systems), naive-UTC discipline, boot-epoch amnesty semantics, `advisory_backfill` rows excluded from live census, `-1`/None fail-open sentinels ≠ 0, SQLite/PG dual-driver columns, monotonic stamps in breakers.

**S6.3 — Dimensionality gaps (name-only, no scoping):** census = current-state-only (no history); worker pool = no per-lane labels, no durations, no failure classes; LLM = no labels at all; task = no per-agent/model/project (join required), no percentiles; usage tables = cumulative-only (snapshot) / no retention (all); T2 attribution = estimate-by-physics; snapshot = 2 fixed scopes; breakers = instantaneous state only; heartbeat = no persisted staleness episodes; mission = no mint-rate/latency, failures invisible.

---

## Breadcrumb corrections (evidence-based)

1. T1/T2/T3/T4 = table ordinals, not flush tiers; resource family = **4** tables (added `resource_daemon_bucket`, `resource_pid_map`). 2. R-10 wedge census defect **already repaired** at this ref. 3. db_offload = **6 workers**; "capacity 25" = readiness F4-A admission capacity (`readiness.py:195`). 4. Readiness has **no** loop-lag signal; the v0.27.11 "250/500ms bounds" were test-side assertions. 5. "A=438 B=175 C=4 D=148 E=14 F=2 (781)" = `scripts/audit/borrow_census.py` **static AST audit** of DB-borrow sites (family-F drift rule §7.3), not runtime metrics. 6. Absence check: no prometheus-client/OTel/statsd/psutil deps; exposition = hand-rendered Prometheus text over own-stdlib registry (default OFF). 7. `metrics-collection` planning docs are **committed** at `origin/latest` (not merely untracked). 8. WS-B "metrics lane" = the full registry/listener/gate/census subsystem (+3224/−250), not just a census module.

## Unverified items (🔴/🟡, from the strands — none blocking)

Whether anything live-scrapes `:9798` (host state, not code) · per-family completeness of `/api/metrics` render vs registry (B 🟡) · `WatchReconcileSweepService` aggregate reads (D 🟡) · `instance_execution_leases` per-site wiring (D 🟡) · `service_tracking` writer internals (D 🟡) · event-table per-kind emission sites (E 🔴) · exhaustive retention sweep for drift/maintenance/prompt tables (E 🔴) · non-streaming LLM usage path (E 🔴, moot — no consumer) · T2 top-N sort key (A 🟡) · anomaly-store eviction knob (A 🟡) · FE consumers of `/api/snapshots/metrics` (sibling strand's territory).

## Completeness

**No gaps** — all 5 dispatched workers reported and were adjudicated 🟢 on evidence (resource A, loop/pool B, registry C, core-family D, usage-misc E), plus 3 self-run verification passes (ref pin + migration census + absence greps) and the committed-catalog read. All 5 workers completed; zero of my instances remain live.
