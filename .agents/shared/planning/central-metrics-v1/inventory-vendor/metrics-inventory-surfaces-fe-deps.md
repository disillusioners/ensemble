<!-- VENDORED COPY — provenance header (AM-24, architect v2 §4 / §8 M-10) -->
<!-- Source path (READ-ONLY shared main checkout, NEVER modified by this lane): -->
<!--   /home/nea/ensemble-src/.agents/shared/planning/metrics-unification/metrics-inventory-surfaces-fe-deps.md -->
<!-- Source status: UNTRACKED in the shared main checkout (not in git history) — vendored here so committed citations stop dangling (AM-24). -->
<!-- Evidence ref: origin/latest@62627265f (v0.28.4-era) per the inventory's own provenance line. -->
<!-- Vendored: 2026-11-02 (UTC) by the central-metrics-v1 plan set (planner). Bytes below are byte-identical to the source; only this header was prepended. -->
<!-- Original sha256 + verification recipe: inventory-vendor/PROVENANCE.md -->

# Metrics Unification Inventory — Exposed Surfaces, Internal Consumers, Frontend, Dependencies

**Commission:** read-only evidence-gathering for the unified central metrics system architecture discussion (surfaces strand). Facts only.
**Ref used:** `origin/latest = 62627265f` ("! New version 0.28.4", 2026-11-01 19:53 +0700) — newer than the expected a84af3ce1/v0.28.3; fallback tag v0.28.3→a84af3ce1 recorded but NOT needed.
**Method:** all evidence via the git object DB (`git grep`/`git show`/`git ls-tree` against SHA `62627265f`). The stale v0.27.8-era working tree was never read; zero writes except this file. Three worker investigations, controller spot-checked (4–6 citations each reproduced verbatim).
**Scope split:** a sibling strand owns CORE-family **producers** (worker/job/task/mission/queue, LLM usage, loop/wedge/readiness/DB-pool producers, metrics-registry lane). This report owns **exposed surfaces** (REST + agent tools), **core-family consumers**, **frontend**, **dependencies**, and first-class deep-dives of the **SNAPSHOT** and **SKILL** module-local metrics systems.

---

## §0 DISCOVERY (read first): an in-flight "v2 Metrics Collection" lane already exists in the repo

`.agents/shared/planning/metrics-collection/` (`plan-overview.md`, `architecture-recommendation.md`, `decisions.md`; dated 2026-10-29, planner[v2], status Draft/Ready for Review, branch `feature/metrics-collection @ 9b8437c9f`) — a full central-metrics plan whose objective is "(b) scrapeable by stock Prometheus + Grafana with zero custom parsers … (c) <1% host CPU". Key ratified content (quoted from plan-overview at the SHA):

- **Direction already seeded:** Prometheus+Grafana is the *target*, not open — the user's "common stack vs build-own" question has an in-flight answer: **build-own registry (stdlib, hand-rendered Prometheus text exposition, NO prometheus-client dep) + stock Prometheus/Grafana scrape**.
- **27 catalogued metric families** (F0.1–F0.4, F1–F22 + F7b), incident-driven (every Oct-26/28 incident must fire an alert within 5 min from these metrics alone): pool leaks (`ensemble_pg_backends{state}`, `ensemble_pg_idle_in_txn_oldest_seconds`), loop starvation (loop-lag histogram, `ensemble_thread_cpu_seconds`), checkpoint read latency (`ensemble_checkpoint_op_seconds` extended buckets), readiness false-degrade (`ensemble_readiness_probe_total`, `ensemble_readiness_grace_active`), sweeps (`ensemble_sweep_cycle_seconds{lane}`), pools (`ensemble_db_pool_*{pool}`), etc. Cardinality budget: ≤2k daemon-origin series (MA-10).
- **Architecture decisions:** separate **localhost-bound `/metrics` listener** (127.0.0.1, managed key `limits.metrics_listener_port`, default 9798) — NOT on the public 9797 port; `/metrics` is "a view over the same services, not a second implementation" (samplers shared with resource_monitor; **Prometheus authoritative for alerting series, resource_monitor PG tier authoritative for per-instance views**); out-of-process witnesses (node_exporter, postgres_exporter, textfile `*.prom` collector) because a wedged daemon cannot export its own metrics; kill-switch default OFF.
- **Explicit out-of-scope (directly relevant to onboarding):** skill-metrics lane `/api/skills/{id}/metrics` (decision m5 — "already has its own surface; re-surfacing in /metrics would duplicate and double-count"); per-instance high-cardinality breakdowns; k8s; VictoriaMetrics/Mimir compat.
- **Deploy-target note (plan's claim):** host binaries / VM compose; plan states PROD host is macOS (WS-A correction) ⇒ no `/proc`/cgroups — resource_monitor producer "dark in prod" until the WS-A merge (`feature/resource-monitor-flusher-fix@53547b915`).
- **Already LANDED at 62627265f (the P0 seed):**
  - `daemon/routers/metrics.py` — `GET /api/metrics` (mounted api.py:5278): ONE route, body = verbatim `daemon.checkpoint_metrics.render_metrics()` (Prometheus text v0.0.4), scope `maintenance:read`, **503 fail-closed when auth is OFF** ("a refusal never renders a metric", D3).
  - `daemon/services/metrics_listener.py` — localhost-only 127.0.0.1 listener, single `GET /metrics`, never-503 (MA-15), classic text exposition (MA-16); own port/thread.
  - `daemon/services/metrics_gate.py` — kill-switch `ENSEMBLE_METRICS_ENABLED` + managed key `limits.metrics_enabled` (app_settings DB tier, refresher thread); explicit-unset ⇒ managed-key default ⇒ **OFF**; unrecognized explicit value fails closed.
  - `daemon/checkpoint_metrics.py` — the in-RAM metric registry (checkpoint read-path counters `checkpoint_read_*`, `checkpoint_read_size_bytes` histogram, F0.1 families `ensemble_metrics_up`/`scrape_requests_total`/`exposition_seconds`).

**Implication for the discussion:** the central BE already has a registry + gate + dual exposition surfaces (authenticated REST + loopback listener) to grow into; module onboarding (S6) plugs *into this lane* rather than into a blank slate.

---

# S3 EXPOSED SURFACES TODAY

## S3.1 REST endpoints serving metrics/resource/telemetry/census data

Router mounting verified at `daemon/api.py:5233-5392`. Shapes = top-level field names.

| Path + Method | Router file:line | Response shape (top-level fields) | Auth | Backing store | FE consumer |
|---|---|---|---|---|---|
| `GET /api/metrics` | metrics.py:110 | **Prometheus text body** (not JSON), `text/plain; version=0.0.4` | scope `maintenance:read`; **503 fail-closed if auth OFF** | in-RAM registry `checkpoint_metrics.py` | **NONE** (0 FE hits) |
| `GET /livez` | api.py:5436 | `{status:"alive", uptime_seconds, version}` | public (probe-tag list api.py:4901-5001) | event loop only | NONE |
| `GET /readyz` | api.py:5452 | `{status, components{database,queue_freshness,services,checkpoint_saver}, detail{}, draining, auth_dependency_status}` (models/common.py:150-215; handler zero-DB, cached composite) | public probe | background-refreshed `ReadinessComposite` (readiness.py) | **NONE** — detail has no FE reader |
| — `/readyz` `detail.*` | readiness.py:504-606 | `reasons[], queue_max_age_seconds(+reason measured/empty/probe_timeout/unknown), checked_at, inflight_turns, component_classes{}, grace{grace_active,grace_reason,grace_expires_at,grace_scope,grace_holds}, leak_signal{detected,checkedout_rising,streak,reason}, pool_state{sqlalchemy,psycopg}, resume_hold{}, burst_signals{}, mcp{<server>:…} (ADVISORY only), skill_sources{}` | — | in-RAM composite | consumed only by promote/soak shell + humans |
| `GET /api/resources/live` | resources.py:276 | `{ts, sample_level, daemon{}, cgroup{}, children[], instances[], attribution{}, overruns, self_cost{}}`; degraded→empty shape, never 503 (D11) | none (default posture) | sampler ring + counters | ✅ live-tab, **8 s poll** |
| `GET /api/resources/history` | resources.py:458 | `{points[], resolution_used, rollup, truncated}` — tier predicate raw/minute/hour (:517-540) | none | `resource_instance_sample` PG table | ✅ timeline-tab (manual refresh) |
| `GET /api/resources/self-cost` | resources.py:694 | `{cpu_pct, cumulative_cpu_s, buffers_bytes, rss_budget_mb, threads, samples, ticks_skipped, flush{batches,bytes,errors}, profiling{active,capabilities}}` | none | sampler+flusher counters | ✅ live-tab, 8 s |
| `GET /api/resources/traceback` | resources.py:983 | `{window{t0,t1}, instances_alive[], in_flight_tasks[], spawned_children[], structural{queue_depth,workers,checkpoints,gc_events}, findings[]}` | none | join layer over instance/task/process rows | ✅ timeline drill-down |
| `GET /api/resources/anomalies` (+`/{id}/evidence`) | resources.py:1080/:1121 | `{anomalies:[{id,kind,t_start,t_end,peak,baseline,severity,series_key,shape,instance_id?}]}` (FE-frozen) | none | anomaly journal (MAD robust-z, window 600s) | ✅ timeline markers |
| `GET /api/resources/control-capabilities` | resources.py:1199 | `{enforcement:"observe-only", actions[{name,user_gated,available:false}], policy_rows}` — every action `available:false` (D4) | none | live COUNT of `resource_limit_policy` (0 rows by design v1) | **NONE** (service layer omits it — "belongs to P6", resources.service.ts:26) |
| `POST /api/resources/profile` | resources.py:1325 | `{accepted, scope, kind, requested_by, seconds, clamped, active_until, capability, profile_seam_invocations}` | none | **SEAM ONLY** — in-RAM reservation, no profiler (D2b) | **NONE** |
| `GET /api/wedge-captures` (+`/status`, POST `/capture`, `GET /{id}`) | wedge_capture.py:159/:231/:268/:335 | `{captures[], count, source}` / `{enabled, running, auto_continue_mode}` / capture detail | scope `maintenance:read` (+origin guard on POST) | filesystem capture dirs (authority) + `wedge_captures` rows | **NONE** |
| `POST /api/recovery/recover_report_delivery` | recovery.py:39 | `{recovered, skipped_busy, busy_check_failed, already_recovered, orphan_disposition, errors, …}` (report_delivery_recovery.py:524-606); 503 when disabled | scope `auth:admin` | on-demand RDRS sweep | **NONE** |
| `GET /api/notifications/stream` | notifications.py:29 | SSE frames | none (SSE) | notification service queue | ✅ notification.service (SSE) |
| `GET /api/mcp-servers` | mcp_servers.py:326 | `McpServerInfo{…, health_state}` — `health_state` appended per row (:298-308) from per-boot quarantine registry `daemon.mcp.init_guard` (ok/degraded/quarantined/None) | scope `mcp:read` | MCP config rows + in-RAM registry | ✅ mcp-server.service — **but `health_state` is NOT in any FE model (0 hits) — served-but-ignored** |
| `GET /api/instances` | instances.py:375 | `{instances:[InstanceInfo{…,pending_count}], total, limit, offset, has_more, truncated}` | scope `instances:read` | instance repo + per-row queue stats (:366,596) | ✅ home (10 s), instance.service (8 s); **REST `pending_count` unused by FE — FE takes it from SSE** |
| `GET /api/instances/{id}` | instances.py:556 | `InstanceInfo` | `instances:read` | instance repo | ✅ instance pages |
| `GET /api/jobs` (+`{id}`, POST) | jobs_crud.py:725/:659/:448 | `{items?, total}` — **total = len(page) under newest-100 clamp** | `jobs:read/write` | job queue | ✅ badge (8 s `?status=queued,active`) + jobs page (30 s) |
| `GET /api/jobs/cleanup/preflight` | jobs_management.py:604 | `{bad_state_count, zombie_instance_count, live_instance_count, live_instance_ids[], defer_blocked_count}` — pure COUNT queries | `jobs:read` | job/instance rows | ✅ cleanup dialog (manual) |
| `GET /api/work` | work.py:109 | `list[WorkRecord.to_dict()]`: `{work_id, kind, status, instance_id, project_id, agent_id, result_summary, error, created_at, started_at, completed_at, message_id, job_type, mission_liveness}` — no pagination | default posture | work resolver (virtual view over jobs+tasks) | ✅ jobs page (30 s) |
| `GET /api/queues` (+`/{id}`, `/defer-blocked`) | queues.py:153/:289/:599 | queue list/detail; defer-block = hold state + witnesses | default posture | queue registry / DeferBlockResolver | plain `/api/queues`: **NONE**; `/defer-blocked`: ✅ badge + jobs page |
| `GET /api/snapshots/metrics` | snapshots.py:257 | `{capture_counts:{<agent_id>:{created:int}}, spawn_counts_per_snapshot:[{snapshot_id,count}]}` (schemas.py:1783-1817); fail-soft empty shape | default posture | `snapshot_usage_counters` via `SnapshotMetricsService.surface()` | ✅ snapshots page (manual/cached signal) |
| `GET /api/settings/snapshot-usage-metrics` | settings.py:791-832 | same shape — **DEPRECATED alias**: headers `Deprecation: true`, `Sunset: Sun, 31 Dec 2026`, `Link: </api/snapshots/metrics>; rel="successor-version"` (RFC 8594/8288) | default posture | proxies canonical handler | FE uses canonical path |
| `GET /api/settings/daemon` | settings.py:943 | frozen FE settings contract | `settings:read` | settings resolver | ✅ settings page |
| `GET /api/dlq` (+replay/get/delete) | dlq.py:246/:292/:383/:431/:533/:600 | `{items[], total}` (total = pre-pagination count) | default posture | dead-letter service | **NONE** (0 FE hits) |
| `GET /api/maintenance/checkpoint-cleanup/{availability,status}` + `dry-run/execute/runs` | maintenance.py:135/:174/:210/:251/:307 | kill-switch shape / config+last-run / would-delete counts | origin guard + `MAINTENANCE_ENDPOINTS_ENABLED` | MaintenanceService | ✅ maintenance page (2 s timer during active run) |
| adjacent: `GET /api/mcps-gateway/{machines,sessions,audit}` | mcps_gateway_read.py (api.py:5347) | gateway tables (audit = usage-ledger view) | — | `mcp_gateway_audit` | ✅ mcps-gateway page, 10 s |
| adjacent: `GET /api/missions` | api.py:5243 | mission read projection | — | mission tables | ✅ badge leg (8 s forkJoin) |

**Sweep verdict:** no additional first-class metrics endpoints beyond the above (`git grep -iE 'metrics|telemetry|census|resource_|_sample|health_state|stats'` over `daemon/routers/`).

## S3.2 Agent tools that read metrics/resource data (with allow-lists)

Two-layer auth everywhere: `tools.allow` category in meta.json **AND** hard-coded agent-id frozensets inside the tool modules.

| Tool | Category | Module:line | Returns | Allow-list (verified meta.json) |
|---|---|---|---|---|
| `instance_list` | `instance_read` | instance_read.py:592 | fleet census rows: `{instance_id, agent_id, status, liveness, parent_id, children_count, age_seconds, idle_seconds, stale, pause_marker, pending_question_pack, pending_count}` (bounded: 200/page, 1000×20 windows, 10 s budget) | **maintenancer only** — `INSTANCE_READ_AUTHORIZED_AGENTS = {"maintenancer"}` (instance_read.py:128) |
| `instance_get` | `instance_read` | :788 | one instance hydrated (redacted metadata + ≤20 messages) | maintenancer only |
| `instance_health` | `instance_read` | :1034 | wedge report — 7 detector classes (stale-running, waiting-on-dead-parent, question-paused-no-task, zombie-pending, orphans, failed-unreported, never-dispatched-husk) + `scan_truncated`/`evidence_legs` honesty fields | maintenancer only |
| `fleet_capacity_summary` (+ alias `instance_fleet_summary` :1242) | `instance_read` | :1154 | **richest metrics payload in the tool plane**: counts_by_agent/status/liveness, per-queue `{pending,active,dead}`, worker_pool `{size,busy,idle,saturated}`, `dlq_depth`, `dead_jobs_total` (+`dlq_note`), totals reconciliation, `audit_pending{count,oldest_age_seconds}` | maintenancer only |
| `instance_terminate/reclaim/nudge/mark/answer/dismiss` | `instance_ops` | instance_ops.py:854/:1310/:1492/:1738/:2025/:2180 | act outcomes (`dismiss` echoes `audit_row_id`) — not metrics readers | **maintenancer only** (`INSTANCE_OPS_AUTHORIZED_AGENTS` :167; kill-switch `ENSEMBLE_INSTANCE_OPS_ENABLED` default ON) |
| `wedge_capture_list` / `wedge_capture_get` | `wedge-capture` | wedge_capture.py:190/:226 | capture manifest + artifact set (reads FILESYSTEM capture dir, not table) | **{ari, leader, maintenancer}** (WEDGE_CAPTURE_AUTHORIZED_AGENTS :84; meta.json verified all three) |
| `board_create/update/list/get`, `board_link` | `board`, `board-link` | board_tools.py / board_link.py | work items (not metrics-native) | **ari only** (v0.26.2 ruling holds) |
| `self_alarm`, `view_write`, `notify_user`, `view_link` | `self-alarm`, `view-write`, `notify-user`, `view-views` | self_alarm.py / view_write.py / notify_user.py / live_views.py | lateness math / report pages / notification ledger | ari only (view_link: ari+leader+designer) |
| `snapshot_create` / `snapshot_search` | literal tool names | snapshot_tools.py:451/:648 | snapshot ops — NOT metrics readers (write participants only) | worker, tester, coder |
| `db_conn_*`, `db_postgres_dml_select` | `db` | db_tools.py:274-514 | arbitrary SELECT vs **registered external** connections (ad-hoc metrics channel if a daemon-DB conn is registered — gap material) | maintenancer, devops, wanderer, tester |
| `ens_db_postgres_select` / `ens_db_inspect` / `ens_db_repair_execute` | `ens-db` | ens_db_tools.py:779/:847/:939 | SELECT vs daemon's OWN DB (read-only enforced) / table sizes+row counts+vacuum state / nonce-gated write | **maintenancer only** |
| **`ens_db_pool_status`** | `ens-db` | ens_db_tools.py:1184 | `engine.pool.status()` for shared read + repair engines — **the only agent-facing DB-pool metrics** | maintenancer only |
| `ens_system_log_list/read/search/tail` | `system-log` | system_log_tools.py:278+ | daemon log forensics (the only reader of metrics_census output, see S3.4) | maintenancer + worker |
| `job_*` family incl **`job_progress`** :3262 (`{status, elapsed_seconds, last_assistant_message, instance_tree{total,active,completed}}`) | `job` | job_queue.py | turn progress — **no tool returns live queue depths** (that is `fleet_capacity_summary` only) | ari, jober (leader: job_pause/job_resume literals only) |
| `system_health` | `system` | system.py:502-538 | **STATIC** `{version, database_type, rag_enabled, python/platform, data_directory, pid}` — config masquerading as health | **gaia only** |
| `proc_*`, `service_*` | `proc`, `service` | proc_tools.py / service_tools.py | weak process-health carriers (pid/status/exit_code/uptime) | devops, wanderer, worker, tester, coder, giter, designer, planner, reviewer |
| `skill_feedback` | `dynamic-skill` | skill_tools.py:1394 | subjective metric write (see S3.6) | 20 agents incl. all three targets |
| `shared_meta_kv`, `context`, `ens_env_read` | — | — | generic carriers, metrics only if a writer uses them | wide |

**Negative finding (load-bearing):** `git grep 'readiness|loop_lag|pool_state|leak_signal|grace_active|resume_hold' daemon/tools/` → **ZERO hits**. No agent tool exposes readiness components, grace, leak signal, pool state, resume-hold, loop lag, or resource-monitor samples. Those live exclusively on HTTP (`/readyz`, `/api/resources`, `/api/metrics`) + the loopback Prometheus listener. Agents *can* `bash`-curl loopback endpoints, but that is outside any tool contract.

## S3.3 Per-target-agent visibility verdicts (TODAY, at 62627265f)

- **maintenancer** — allow (meta.json:12-22): `system-log, ens-db, knowledge, system_upgrade, system_restart, db, instance_read, instance_ops, wedge-capture`. **Richest of the three**: sole holder of fleet census + 7-detector wedge report + queue/worker-pool/DLQ capacity summary (`instance_read`), daemon-DB SELECT + table stats + the only DB-pool metrics (`ens-db`), wedge captures, daemon logs. **Lacks:** the entire HTTP probe plane — no tool wraps `/readyz` components, `grace_active`, `leak_signal`, `pool_state`, `resume_hold`, loop lag, resource-monitor samples, or the Prometheus registry (loopback-only listener, bind guard listener.py:217).
- **devops** — allow (meta.json:12): `bash, proc, filesystem, time, self, help, image, knowledge, mcp, context, shared_meta_kv, db, infra, project, dynamic-skill, question, midflight, service`. **Zero native metrics surfaces** — no `instance_read`, no `ens-db`, no `system-log`, no `wedge-capture`. Only generic carriers: raw `bash` (can curl `/livez`//`readyz`//`api/resources` or psql — powerful but uncontracted), `db_postgres_dml_select` (external conns only), `service_status/list` pid/uptime. **The most metrics-hungry agent (host/daemon ops) has the thinnest sanctioned read surface.**
- **wanderer** — allow (meta.json:12): `bash, proc, filesystem, time, self, help, explore, mcp, context, shared_meta_kv, rag, instance, blueprint, db, infra, service`. Investigator posture: `instance` category gives deep **single-instance** reads (`get_instance_info` family); `db` external SELECT; bash/service carriers. **No fleet census** (instance_read is maintenancer-gated at instance_read.py:128), no daemon logs, no wedge captures, no pool status. Can describe one instance deeply; cannot natively answer "how sick is the fleet".
- **Contrast:** ari holds wedge-capture + job/job_progress + view-views (chat-side health story); leader holds wedge-capture + view-views + job_pause/resume — neither has instance_read (the v0.26.2 ari-only ruling + v0.27.7 patches hold at this SHA).

## S3.4 Internal consumers (core family, consumer side)

1. **Readiness gates** — `daemon/services/readiness.py`: composite of 4 components (database, queue_freshness, services, checkpoint_saver :93-98); failure classes split grace-eligible (`probe_timeout/pool_busy/queue_stale/read_timeout`) vs never (`pool_leak/hard_component_red/...` :63-77); `GraceTracker` (:754) bounded by `READINESS_GRACE_HORIZON_S`; `leak_signal` from SQLAlchemy pool + psycopg oracle-pool sampling (:658,:685); `resume_hold` promote-txn counters (:416). Served via cached composite refresh loop (api.py:4340, :4416-4450). **Drives:** soak HOLD verdicts, promote auto-rollback, resume holds.
2. **Soak gate** — `scripts/upgrade/lib.sh` `gate_soak` :6046: GREEN/HELD/RESET counters (:6238-6293) — GREEN = livez+readyz hard-green; HELD = livez hard-green + readyz grace-eligible + grace active + leak=0 (greens UNCHANGED); MAX_HOLDS_PER_GREEN=2 (:282); 3 consecutive greens pass (:241); **parses the /readyz body via `_readyz_grace_*`/`_readyz_leak_detected`** — shell-coupled to the readiness payload shape.
3. **Watchover** — conversation-state reader (checkpoint + queue rows), not numeric-metrics.
4. **Graph loop-breaker** — `daemon/graph.py:192-198`: repeated (name,args) tool-call signature, threshold 3, RAM-only state, repair-summary replacement, per-instance repair counter.
5. **Skill-evolution trigger engine** — consumer of skill metrics (full detail S3.6): Tier-1 evaluator `skill_trigger_engine.py:130/:203` over DB-configured rules; fires skill-keeper jobs via `skill_job_dispatcher` (manager.py:7414-7429) — **gated OFF by default**.
6. **RDRS sweep lanes** — `report_delivery_recovery.py`: 6 lanes consult report_injections states, PENDING age (10 min bound :443), sweep interval 300 s (:442), task heartbeat staleness 90 s (repository.py:2055-2109).
7. **Wedge detector** — `daemon/services/wedge_capture/`: triggers `sustained_cpu` (95%-core/30 s) and `sustained_loop_lag` (5.0 s/15.0 s) over trailing windows (triggers.py:54-55,:131-166); watchdog tick → capture (stacks, threads.csv, memory, pool, process, readiness payload :522-532) → filesystem dir (authority, writer.py:38) + `wedge_captures` row. Post-hoc forensics only — nothing auto-enforced.
8. **Scheduler/self-alarm** — lateness math only: `one_shot_max_lateness_seconds` cap (self_alarm.py:596-606,:841-846), 30-min re-arm floor; no counts/heartbeats.
9. **Resource monitor** — `daemon/services/resource_monitor/` + `ResourceMonitorConfig` (config.py:918): 30 s sampler thread (ge15 le60); per-instance cpu/rss + `charged_bytes` registry (charge.py:190-395); self-throttle (1% CPU / 50 MB); 60 s flush batches into `resource_instance_sample` raw + minute/hour rollups; retention ladder **raw 48 h / minute 14 d / hour 180 d** (config.py:979-995); anomaly pass MAD z=6.0. **ENFORCEMENT NONE — observe-only v1**: `resource_limit_policy` empty (models/resource_monitor.py:41-50; row-count endpoint resources.py:1170-1183). Data source: cgroup v2 + raw `/proc` reads — **"NO psutil. Zero new deps"** (proc_readers.py:15).
10. **metrics_census** — `daemon/services/metrics_census.py`: PostgreSQL idle-in-txn backend census by `application_name` site map (one SQL round-trip :130-172; self-excluding; 5 s statement_timeout); cadence `limits.metrics_census_interval_s` default 15 s (floor 15/cap 300); **output lands in the daemon LOG ONLY** (:467) — no table, no API. Only consumer: whoever reads logs (maintenancer via system-log tools).
11. **Prometheus exposition (seed)** — see §0: registry + `/api/metrics` + loopback listener + gate, default OFF, no in-repo consumers yet.

## S3.5 SNAPSHOT system deep-dive (first-class, per addendum)

- **Counters (R16):** table `snapshot_usage_counters` (migration 20260925_233000:36-44; model `SnapshotUsageCounter` repositories/snapshot/models.py:347): `{id, scope, key, value, updated_at}`, UNIQUE `(scope,key)`. Two counter families (models.py:320-321): `capture:agent:<agent_id>` — snapshot_create invocation count (ALL four R9 verdicts: REUSE/NEW/SUPERSEDE/CREATE-FRESH, schemas.py:1800-1806); `spawn:snapshot:<snapshot_id>` — per-snapshot warm-start consumption (cold/no-hit/expired/verify-failed NOT counted, R16 rider j). Monotonic; **monitoring-only by design** (R10 forbids usage-ranking; pinned by `TestMonitoringOnlyPin`).
- **Increment sites (complete):** all funnel through ONE upsert `_increment_upsert` (snapshot_metrics_service.py:206, `INSERT … ON CONFLICT DO UPDATE value=value+1`) ← `inc_capture` (:88) / `inc_spawn` (:105) ← `_safe_inc_*` helpers (snapshot_tools.py:1162,:1176) ← exactly 3 call-sites: snapshot_tools.py:571 (REUSE branch), :636 (success path, real ledger row only), :1043 (finalize_spawn_snapshot warm path, after digest stamp). Fail-soft everywhere. Service wired manager.py:2762-2763.
- **Read surface:** `GET /api/snapshots/metrics` (snapshots.py:257) → `surface()` (:132) → `{capture_counts:{agent:{created}}, spawn_counts_per_snapshot:[{snapshot_id,count}]}`; fail-soft empty shape. Deprecated alias `GET /api/settings/snapshot-usage-metrics` (settings.py:791) proxies the canonical handler; deprecation via response headers `Deprecation: true` / `Sunset: 31 Dec 2026` / `Link: rel="successor-version"` (:830-832).
- **Retention: NONE** — no prune/TTL touches the table; rows accumulate forever.
- **Self-containedness: FULLY contained** — dedicated 254-line stateless `SnapshotMetricsService`; centralization touches only the service + 2 wiring points (manager construction, tools accessor). Router/FE untouched if shape preserved.
- **Seam:** the service itself — swap body preserving the 3-method surface (`inc_capture`/`inc_spawn`/`surface`), or dual-write from the two inc_* methods; `(scope,key,value,updated_at)` is already generic tagged-counter form.
- **Consumers:** FE snapshots page (snapshot.service.ts:55,:106 → snapshots.component.ts:774, snapshot-detail-drawer.component.ts:92); **no agent tool reads it; no internal consumer** (monitoring-only pin). Legacy settings.service.ts:243 method exists but no component calls it.

## S3.6 SKILL system deep-dive (first-class, per addendum)

- **Objective metrics:** counted event = per-injected-skill usage at **task completion**: `SkillMetricsService.record_task_completion` (skill_metrics_service.py:300) from the completion hook (job_queue_service.py:3617) with `{instance_id, agent_id, project_id, task_succeeded, iterations, duration_seconds, task_message}`. Injection registry = `instance_metadata["last_injected_skill_ids"]` (instance_messaging.py:173,:4715; checkpoint-safe context_messages.py:1398). Each completion writes a `skill_usage_records` row (`selected=True`, `applied=False` until feedback, `task_succeeded`, `iterations`, `duration_seconds`, `fallback`) **plus** denormalized bumps on the Skill row (`total_selections`, `total_completions`, `total_fallbacks`, `consecutive_failures` — repository.py:423,:509).
- **Subjective feedback:** tool `skill_feedback` (skill_tools.py:1394; registry name at _tool_registry.py:1532), **category `dynamic-skill`** — held by 20 agents (approver, architect, coder, critic, designer, developer, devops, maintenancer, planner, reviewer, sketcher, skill-keeper, tester, tidier, wanderer, +v2 variants). Writes via `SkillMetricsService.record_feedback` (:921) → stamps the LATEST `skill_usage_records` row for `(skill_id, instance_id)` with `feedback_applied`/`feedback_note`/`feedback_usefulness` (1-10 validated)/`feedback_improvement`; creates row if none (:1090). REST mirror `POST /api/skills/{id}/feedback` (skills.py:1267/:1316).
- **File-skill (`pf:`) lane:** `_feedback_pf_skill` (skill_tools.py:477+) — metrics-ONLY row into `project_skill_usage_records` (v0.28.3; migrations 20261031_120000/1/2; model repositories/project_skill_file/models.py:187 — mirror of SkillUsageRecord minus FK); **no lineage, no evolution dispatch; usefulness/improvement folded into feedback_note** (companion table frozen); knob-gated, fail-closed.
- **Tables:** `skill_usage_records` (models.py:369; DDL 20260710_000001 + cols 20260715/20260721): `{id, skill_id FK, project_id, instance_id, agent_id, task_message, selected, applied, task_succeeded, iterations, duration_seconds, fallback, feedback_applied, feedback_note, created_at, ab_test_group, superseded, feedback_usefulness, feedback_improvement}` — **no `metrics_source` column; system-vs-agent land on the SAME rows** (objective = objective columns + denormalized counters; subjective = `feedback_*` columns). Plus `skill_triggers` (`{id, project_id NULL=global, name, condition_type, condition_json, action, is_enabled}`, models.py:531), `skill_embeddings` (`{skill_id, trigger_query, embedding}` — 3-10 LLM-generated queries per skill, skill_embedding_service.py:207-232), `skill_ab_tests`, denormalized Skill counters.
- **Trigger engine:** rules = DB rows in `skill_triggers`, seeded from `DEFAULT_TRIGGERS` (skill_trigger_seed.py:57+, manager.py:4023-4031). Condition types (skill_trigger_engine.py:130,:203 — pure evaluator): `low_completion_rate`, `high_fallback_rate`, `consecutive_failures`, `task_count_scan` (count-based, selections≥20), `periodic_scan` (daily-ish, last_used>7d), `low_usefulness` (avg feedback <4.0, ≥5 records), `drift_event_observed`. Feed = Skill denormalized counters + `skill_usage_records` aggregates. Firing: maintenance loop `_run_skill_metric_scan` (manager.py:6924, registered :4032-4035, cadence `metric_scan_interval_hours` default 24 h) → enqueue `skill_analysis`/`skill_evolution` JobItems → agent **skill-keeper** (`skill_job_dispatcher.py:267,:432`), **gated by the lane switch — OFF ⇒ INFO refusal** (manager.py:7132-7140).
- **Current state:** `SkillEvolutionConfig.evolution_lane_enabled: bool = Field(default=False)` (config.py:4555, "default OFF — incident 2026-10-21"); canonical env `ENSEMBLE_SKILL_EVOLUTION_ENABLED` wins per-call (skill_job_dispatcher.py:235); no in-repo pin; queued rows terminally refused `lane_disabled` (:4559-4563). Capture lane (`ENSEMBLE_SKILL_CAPTURE_ENABLED`) also default OFF. **Metrics RECORDING runs regardless — only evolution/capture dispatch is gated.** (Live-install `.env` value out of repo scope — 🔴.)
- **skill_bank:** isolated template store — "no lineage, no embeddings, no triggers, no usage records" (skill_bank_repository.py:5); zero metrics coupling confirmed.
- **FE visibility:** Skills page (`skills.component.ts`) → `GET /api/skills`; detail → `/{id}/metrics` (:209), `/{id}/lineage`, `/{id}/ab-test/stats`, `POST /{id}/feedback`; usage rows table (`skill-usage-table.component.ts:16` ← `/{id}/usage-records`); A/B dashboard (`ab-test-dashboard.component.ts:72`); trigger CRUD (`/api/skills/triggers`). **Confirmed limitation:** zero `project_skill`/`skill_sources` references in frontend/src — FE is REST-DB-only; agent-lane file-skill fusion NOT exposed (v0.28.3 disclosure holds).
- **Seam:** the **two public writer methods** of `SkillMetricsService` (`record_task_completion`, `record_feedback`) — every objective row and subjective stamp passes through them (3 callers: completion hook, tool, REST). File-lane boundary = `ProjectSkillUsageRecordRepository.insert` (:335, one caller). Caveat: the trigger engine is a metrics *consumer* — re-pointing `SkillUsageRepository` reads to a central store is a bigger cut than the snapshot adapter (and the metrics-collection plan already rules skill metrics OUT of `/metrics` re-surfacing to avoid double-count, decision m5).

## S3.7 Audit ledgers (usage-metrics-adjacent)

| Table | Writer | Counts |
|---|---|---|
| `notify_audit` | notify_user.py `_ledger_write` :835 → NotifyAuditRepository.insert_accepted (repository.py:82, commit-before-count); refusals :861→:144 | every notify_user attempt + every refusal class |
| `secrets_audit` | secret_service.py `_append_audit_in_conn` :1028 (+:745/:751/:758; call-sites :1481-:1838) | secret lifecycle ops w/ actor |
| `mcp_gateway_audit` | McpGatewayAuditRepository.record (mcps_gateway/repository.py:62; AuditSink services/mcps_gateway/audit.py:166) | gateway commands per agent/machine/tool (args hashed) — FE-exposed via /api/mcps-gateway/audit |
| `instance_ops_audit` | instance_ops.py write-ahead/settle (:868,:1319,:1501,:1751,:2040,:2190 → repository.py:223-235); reader instance_read.py:2137 | privileged act outcomes + refusals (two-tier) |

**notify_user rate counters are DB-BACKED, not in-memory:** N-gate (≥60 s/turn) reads `last_accepted_class_created_at(instance_id)` (notify_user.py:1200 → repository.py:177); M-gate (6/rolling-hour/destination) = fresh SQL COUNT `count_accepted_for_target(target_hash, since=now−1h)` (:1301 → :198-226). In-memory state = per-destination `asyncio.Lock` table only (no counts, notify_user.py:283-309); crash hygiene `sweep_stale_accepted` (repository.py:231).

## S3.8 Dependencies (Prometheus-vs-build-own input)

- **EXPLICIT ABSENCE confirmed in `pyproject.toml` @62627265f** (both `[project] dependencies` and dev groups): **no `prometheus-client`, no `opentelemetry*`/otel, no `statsd`, no `psutil`, no grafana/influx clients, no metrics library of any kind.**
- **psutil NOT used**: resource monitor reads cgroup v2 (`/proc/self/cgroup` → `memory.current`, `memory.stat`, `cpu.stat`, `memory.peak`) + raw `/proc/self/{stat,statm,status}` + per-child `/proc/<pid>/{stat,status}` (proc_readers.py:6-10,:92,:101-102) — "NO psutil. Zero new deps" (:15). Darwin caveat: `/proc` readers cannot answer on macOS (:22-30) — couples to the plan's macOS-prod note (§0).
- Prometheus exposition is **hand-rendered** (`checkpoint_metrics.render_metrics`); metrics router docstring: "NO Prometheus client dependency, NO scrape framework, NO new metric registry" (metrics.py:20-23).

## S3.9 jsonl sidecar / log-file metrics conventions (brief)

| Convention | Writer | Content | Location |
|---|---|---|---|
| `install-audit.jsonl` | install_audit.py:166-173 (O_APPEND) | `{ts, event, name, actor, parent, secret_ref, idempotency_key, trace_id}` | `<workdir>/.agents/shared/planning/designer-agent/install-audit.jsonl` (preferred, :54) / `<data_dir>/` fallback |
| `sync_trail.jsonl` | plugin_subsystem/sync_runner.py:212,:354,:820 | per-run plugin sync trail | `<plugin_dir>/sync_trail.jsonl`; read by promote_staleness.py |
| two-file promote logs | scripts/upgrade/lib.sh:6418-6420, retention :6443-6460 | per-run narrative/payload/client splits + mid-phase markers | `$INSTALL_DIR/data/` run-log dir |
| `upgrade.log` / `launcher.log` | lib.sh:718-735 / :4987,:5179 | raw run output | `$INSTALL_DIR/data/` |
| upgrade journal (JSON) | scripts/upgrade/journal_* | promote lifecycle rows | install root (journal_read.py O(N) reader) |

---

# S4 GAPS & SCATTER (surfaces-side)

1. **Two disconnected planes.** The daemon's self-observability (readiness components, grace, leak signal, pool state, resume-hold, loop lag, resource samples, idle-in-txn census, Prometheus registry) flows ONLY to HTTP/loopback/log surfaces that **no agent tool wraps** (zero grep hits in daemon/tools/). The agent plane meets it only where maintenancer reads logs / ens-db / wedge rows after the fact.
2. **Target-agent asymmetry.** devops (most metrics-hungry) has zero native metrics tools — bash-curl workaround only; wanderer has no fleet census; maintenancer alone holds census/pool/logs. Queue depths exist ONLY inside `fleet_capacity_summary` (maintenancer-exclusive).
3. **Naming scatter across five conventions:** global Prom-text `/api/metrics`; module REST `/api/<noun>/metrics` (snapshots); family REST `/api/resources/*`; nested `/readyz` `detail.*`; colon-namespaced counter keys (`capture:agent:<id>`); plus tool-side naming (`ens_db_pool_status`). Prometheus families use `ensemble_*` (catalog).
4. **Served-but-unconsumed (dead weight today, free onboarding tomorrow):** `/api/metrics` (no FE), `/readyz` detail (no FE), MCP `health_state` (FE model lacks the field), `/api/resources/control-capabilities` + `profile` (FE service deliberately omits), `/api/dlq*`, `/api/recovery`, plain `/api/queues` (FE only calls defer-blocked).
5. **REST-vs-DB divergence precedents:** FE Skills page REST-DB-only while agent-lane fuses file skills (v0.28.3); REST `pending_count` dead (FE uses SSE value); `/api/jobs` total is a clamped projection (jobs-window.model.ts guards).
6. **Duplication guards already ruled:** skill metrics excluded from `/metrics` re-surfacing (plan m5 — double-count); snapshots alias deprecated with RFC-8594 headers — the central system must respect both.
7. **Snapshot counters:** no time dimension (monotonic + `updated_at` only), no event log, no retention bound; per-agent and per-snapshot labels only.
8. **Skill file lane:** `project_skill_usage_records` = feedback-only, zero readers, usefulness/improvement folded into a text note (columns frozen) — a strictly poorer mirror of the DB lane.
9. **metrics_census:** computes durable-worthy data every 15 s but lands in logs only — no table, no API (TIER-3 for central ingestion as-is).
10. **`system_health` tool is static config data masquerading as health** (gaia-only) — naming hazard for a unified system.
11. **Audit ledgers** are durable event histories with no aggregation layer — counts (e.g. notify rate gates) are computed by fresh SQL per call.

---

# S6 SEAM ASSESSMENT — reframed for greenfield-first phasing (central BE+FE first; modules onboard in later waves; module-local sparseness anticipated)

## S6.1 Onboarding difficulty tiering

| Surface | Tier | Justification |
|---|---|---|
| **Resource monitor (PG tier)** | **TIER-1** | durable tiered time-series (`resource_instance_sample` + rollups, retention ladder 48h/14d/180d), REST `/api/resources/history` already time-series shaped, FE chart exists; read-only poller/adapter suffices |
| **Snapshot metrics** | **TIER-1** | durable `snapshot_usage_counters`, stable generic `(scope,key,value,updated_at)` shape, single-funnel 3-method service, REST + FE exist; wrap `surface()`/table |
| **Skill metrics (DB lane)** | **TIER-1** (with coupling caveat) | durable per-event rows (timestamps, per-skill/instance/agent/project labels) + REST + FE; caveat: trigger engine + A/B stats read aggregates via `SkillUsageRepository` and the plan already excludes re-surfacing in `/metrics` (m5) — onboard as PULL source, don't make central the writer |
| **Audit ledgers (notify/secrets/mcp_gateway/instance_ops)** | **TIER-1** (data) | durable append-only rows with timestamps; SQL-pull via existing repos (`ens-db` tools prove the query path); event-shaped, not pre-aggregated |
| **Checkpoint registry + `/api/metrics` + loopback listener** | **TIER-1 as interface** / TIER-2 as history | this IS the central seed (registry + gate + exposition already landed, §0); in-RAM only (no durability across restarts), default OFF — history/durability is central-BE work |
| **Job/queue/work stats** | **TIER-2** | REST-only point-in-time projections over live rows; no durable metrics series; `/api/jobs` total clamped; central needs a poller today, native instrumentation later |
| **Instance census** | **TIER-2** | computed-on-demand over state tables (instances + queue stats); no census history; adapter = poll `/api/instances` or reuse `instance_read` builders |
| **Readiness `/readyz` detail** | **TIER-2** | ephemeral cached composite, no durable rows; pull-able now (GET), but HIGH consumer coupling — soak-gate shell parses `grace`/`leak_detected` fields, shape is de-facto frozen |
| **MCP `health_state`** | **TIER-2** | ephemeral per-boot registry state (ok/degraded/quarantined), no transition history; FE ignores it today ⇒ zero coupling, cheapest TIER-2 to onboard |
| **Wedge captures** | **TIER-2** | durable but event-shaped forensics (FS dirs authoritative + rows); sparse (trigger-gated); needs extraction/normalization, not new instrumentation |
| **metrics_census (idle-in-txn)** | **TIER-3** | computed (15 s cadence) but lands in LOG only — needs a new sink (table/registry family) to be centrally ingestible |
| **Loop-breaker state** | **TIER-3** | RAM-only per-instance counters; name-only for central purposes |
| **Soak counters** | **TIER-2/3** | per-run shell state; only upgrade-journal traces survive; per-run, not continuous |
| **project_skill_usage_records (file lane)** | **TIER-2/3** | table exists but feedback-only, zero readers, knob-gated — onboard by reading the DB lane instead |

## S6.2 Registration contract inputs (per surface)

| Surface | PULL point today | PUSH hook (if any) | Durability | Consumer coupling (breaks/duplicates if central becomes source of truth) | Naming/labels observed |
|---|---|---|---|---|---|
| Resource monitor | `/api/resources/history` + PG table | flusher batches (60 s) | durable, tiered | FE timeline (manual refresh); observe-only policy endpoints unused | per-instance series (cpu/rss/charged_bytes); tiers raw/minute/hour |
| Snapshot metrics | `GET /api/snapshots/metrics`; table | `SnapshotMetricsService.inc_capture/inc_spawn` (single funnel) | durable, unbounded | FE snapshots strip + deprecated alias contract (Sunset header) | colon-namespaced `scope:key` |
| Skill metrics | `GET /api/skills/{id}/metrics,/usage-records,/ab-test/stats`; tables | `SkillMetricsService.record_task_completion/record_feedback` (single funnel) | durable per-event | trigger engine reads, A/B stats, FE skills pages, REST feedback POST (write path), plan m5 double-count rule | snake_case columns; feedback_* prefix; ab_test_group |
| Audit ledgers | SQL (repos); `/api/mcps-gateway/audit` (FE) | repository insert methods | durable append-only | notify rate gates COUNT fresh per call (do not cache without care); FE gateway audit table | `<noun>_audit` tables; actor/attribution columns |
| Checkpoint registry | `GET /api/metrics` (scope maintenance:read, 503 fail-closed) + loopback `:9798/metrics` | registry increment API in checkpoint_metrics.py | in-RAM (lost on restart) | none in-repo yet; gate default OFF | Prometheus families `ensemble_*`; labels per catalog |
| Jobs/queues/work | REST (`/api/jobs`,`/api/work`,`/api/queues/defer-blocked`,`/api/jobs/cleanup/preflight`) | none | none (live rows only) | FE badge 8 s forkJoin (5 endpoints!), jobs page 30 s | status enums; work `kind` |
| Instance census | `/api/instances`; `instance_read` builders | none | none (state tables) | home 10 s, instance.service 8 s, badge leg; tools maintenancer-only | status/liveness enums; pending_count |
| Readiness | `GET /readyz` (cached composite) | composite refresh loop | ephemeral | **soak-gate shell parsers `_readyz_grace_*`/`_readyz_leak_detected` — shape frozen**; promote auto-rollback semantics | `detail.*` nested keys; component names |
| MCP health | `/api/mcp-servers` field | quarantine registry transitions | ephemeral per-boot | none today (FE drops the field) | state enum ok/degraded/quarantined |
| Wedge captures | REST + tools + FS dirs | watchdog trigger → capture | durable (dirs authoritative) | maintenancer/ari/leader tools | capture dirs, manifest.json, `wedge_captures` rows |
| metrics_census | daemon LOG only | census tick (15 s) | none (log lines) | log readers (system-log tools) | `application_name` site map |

## S6.3 Dimensionality/granularity gaps (facts, no fix design)

- **Resource monitor:** best-in-class (time-series + rollups + retention) but labels are per-instance only — no per-agent/per-project/per-tier aggregate vocabulary; anomaly journal flattened for FE.
- **Snapshot counters:** no time dimension at all (monotonic totals + last-updated); labels = agent / snapshot_id only; no increment-event history.
- **Skill usage records:** good dimensionality (per-event rows: skill×instance×agent×project×time×outcome×iterations×duration) — gaps: no `metrics_source` typing (objective vs subjective share rows), file-lane mirror lacks usefulness/improvement columns, no retention policy identified.
- **Jobs/queues/work:** point-in-time only — no history, no per-queue time series, `/api/jobs` total clamped at newest-100.
- **Instance census:** point-in-time; no census snapshots/history; liveness derivations not persisted.
- **Readiness:** point-in-time composite; leak streak/grace state in-RAM; only wedge captures occasionally freeze a readyz.json.
- **MCP health:** state enum only, no transition/event history.
- **Checkpoint registry:** counters+histogram with labels but in-RAM — restart resets all series.
- **Audit ledgers:** full event history (durable) but zero aggregation/rollup layer.

## S6.4 FE foundation note (charts come FIRST)

- **Charting-lib verdict: NO charting library exists** in `frontend/package.json` (Angular ^21.2.5, CDK/Material ~21.2.5, ng-zorro-antd ^21.2.1, CodeMirror, marked, mermaid ^11.4.0 — mermaid renders diagrams-in-markdown, NOT data charts; everything else scanned negative). The only chart-like rendering today is **hand-rolled inline SVG** (`pages/resources/svg-timeline/svg-timeline.component.ts:88-131,:193-281` — string-composed `<svg><path>` area/line). ⇒ central FE charts = **new-dependency decision** (ngx-charts / chart.js wrapper / apexcharts…) or extend the hand-rolled SVG pattern; and note the in-flight plan targets **Grafana** for dashboards — in-app FE charts are the complementary developer-facing surface.
- **Natural first consumers/sources for central chart data (endpoint + cadence):** `/api/resources/history` (only true time-series REST today; timeline-tab, manual refresh); live-tab `/api/resources/live` + `/self-cost` (8 s); jobs badge 8 s forkJoin over 5 endpoints (`/api/jobs?status=queued,active`, recent-10, missions, instance tree, defer-blocked); jobs page `/api/jobs` + `/api/work` + defer-blocked (30 s); home `/api/instances` (10 s); instance.service (8 s); mcps-gateway (10 s); command-state (5 s); vscode status (2 s); SSE streams (notifications, instance events incl `pending_count`, job events, migration). These flows define the FE's de-facto pull contract a central BE would replace or back.

---

# S7 FRONTEND — today's metrics-ish UI inventory + charting verdict

- **Framework:** Angular 21.2.5 standalone (+ Material/CDK 21.2.5, ng-zorro-antd 21.2.1). **Charting lib: ABSENT (explicit).** Hand-rolled inline SVG timeline is the only visualization primitive.
- **Components rendering metrics-ish data:**

| Component | Renders | Source | Cadence |
|---|---|---|---|
| `pages/resources/live/live-tab.component.ts` | stat cards / text rows | `/api/resources/live` + `/self-cost` | 8 s (POLL_INTERVAL_MS=8000 :57) |
| `pages/resources/svg-timeline` + `timeline/timeline-tab` | SVG line/area + anomaly markers + drill-down | `/api/resources/history`, `/anomalies`, evidence+traceback forkJoin | manual (init + refresh) |
| `components/job-queue-indicator` | badge count + liveness tooltip | forkJoin: jobs-active, recent, missions, instance-tree, defer-blocked | 8 s (:130) |
| `pages/jobs/jobs-page.store.ts` + `jobs.component.ts` | tables + counts + cleanup dialog counts | `/api/jobs`, `/api/work`, `/api/queues/defer-blocked`, cleanup/preflight | 30 s (jobs-poll.model.ts:65) / manual |
| `pages/home/home.component.ts` | instance list | `/api/instances` | 10 s (:108-111) |
| `services/instance.service.ts` | instance list state | `/api/instances` | 8 s (:418) |
| `pages/snapshots/*` | counters strip (plain text) | `/api/snapshots/metrics` | manual/cached signal |
| `pages/skills/*` (skills, skill-detail, skill-usage-table, ab-test-dashboard) | metrics bundle, usage table, A/B stats, triggers | `/api/skills`, `/{id}/metrics`, `/{id}/usage-records`, `/{id}/ab-test/stats`, triggers | manual |
| `pages/mcps-gateway` | machines/sessions/**audit** tables | `/api/mcps-gateway/*` | 10 s |
| `pages/maintenance/checkpoint-cleanup` | run progress | `/api/maintenance/checkpoint-cleanup/*` | 2 s during active run |
| `pages/settings` | settings + vscode status | `/api/settings/daemon` etc. | vscode 2 s |
| SSE services | toasts, per-instance events ("N pending" from SSE `pending_count`, NOT REST), job events, migration | `/api/notifications/stream`, `/api/instances/{id}/events`, `/api/jobs/{id}/events`, `/api/migration/events` | persistent SSE |
| `services/command-state.service.ts` | active-command fallback | `/api/instances/{id}/commands/active` | 5 s |

- **Limitation patterns (FE shows subset of backend):** Skills page REST-DB-only (v0.28.3, confirmed — zero project_skill refs in FE); MCP `health_state` served-but-unmodeled; `/readyz` detail + `/api/metrics` + `/api/dlq` + `/api/recovery` + `/api/queues` (plain) + resources control-capabilities/profile = zero FE consumers; REST `pending_count` superseded by SSE.

---

## Unverified / out-of-repo items

- 🔴 Live-install `.env` values (`ENSEMBLE_SKILL_EVOLUTION_ENABLED`, `ENSEMBLE_METRICS_ENABLED`, listener port) — runtime state, not in the git tree; in-repo defaults are OFF for both lanes.
- 🟡 REST auth posture actually enforced on `/api/resources/*`, `/readyz`, `/api/snapshots/metrics` in a given deployment (auth disabled ⇒ default posture) — routes inspected for shape, not live-tested.
- 🟡 Exact field lists: `EvidenceResponse`, `DeferBlockResponse`, `JobQueueResponse` bodies (paths/purposes verified; models not fully expanded). `ab-test-dashboard` direct data source not traced.
- 🟡 `settings.service.ts:243` legacy metrics method appears uncalled (grep-based; dynamic references would not show).

## Provenance

- Workers: metrics-rest-fe (REST+FE+deps+jsonl), metrics-tools-consumers (tools+verdicts+consumers), metrics-snapshot-skill (snapshot+skill deep-dives+ledgers) — all read-only against SHA 62627265f; controller spot-checked 10 citations across the three reports (all reproduced).
- Related in-repo artifacts to read next: `.agents/shared/planning/metrics-collection/{plan-overview,architecture-recommendation,decisions}.md` + metric catalog (referenced as `metric-catalog.md`), `daemon/checkpoint_metrics.py`, `daemon/services/{metrics_listener,metrics_gate}.py`.
- Companion strand (sibling wanderer, instance 2c546e63): core-family producers inventory — separate report.
