# Plan Overview: v2 Metrics Collection

Date: 2026-10-29
Author: planner[v2] via plan-creation worker
Status: Draft / Ready for Review
Branch: feature/metrics-collection @ 9bbef71c1 (worktree agents-ensemble-wt-metrics-v2)

## 1. Objective & incident motivation

**Objective.** Ship an incident-driven metrics surface for the agents-ensemble daemon that (a) replays every Oct-26/28 incident as a firing alert within 5 minutes using only these metrics, (b) is scrapeable by stock Prometheus + Grafana with zero custom parsers, and (c) adds <1% host CPU with no pool pressure or loop-lag regression — all while the daemon is wedged (event-loop starvation IS the motivating incident).

**Corrected "Why" (per checkpoint-bench-20261029.md).** The v1 narrative attributed the 17 s checkpoint INSERTs to raw write throughput on Postgres-on-Longhorn. The bench REJECTED that: writes are fast (28 ms/full PUT, 78 PUT/s concurrent, max 254 ms over 6 min sustained; fsync cost ≈2.4 ms). The 17 s event was a stall/contention artifact (WAL-checkpoint window — checkpointer averages 2.3 min write time per checkpoint, 15% forced; or pool contention), not storage throughput. **The real bottleneck is the read path**: cold 1–2.5 MB blob reads cost 1–2.4 s each (scatter of ~2 KB TOAST chunks over Longhorn) because (a) shared_buffers=160 MB vs a 13 GB TOAST, (b) TOAST ~10× bloated with dead chunks from pruned-but-never-reclaimed versions, (c) every checkpoint read is a fresh cache-miss storm. Hence the story is **read latency + TOAST bloat + shared_buffers**, not write throughput. Metric design follows: `ensemble_checkpoint_op_seconds` (CR-3: buckets extended top ≥120 s so 73–99 s reads do not collapse into +Inf) + `ensemble_checkpoint_blobs_table_bytes` (CheckpointBloat alert) + slow-op ledger (existing) as the CheckpointSlow source. Write-side "slow" is now expected to be rare; the metric exists to catch the stall/contention artifact, not throughput.

**Incident → metric table (corrected).**

| Incident (real) | By-hand diagnosis | Metric that replaces it | Alert |
|---|---|---|---|
| P1 pool poisoned by leaked `idle in transaction` conns (Oct 26+28, recurring) | manual `pg_stat_activity` census + fingerprinting | `ensemble_pg_backends{state}` + `ensemble_pg_idle_in_txn_oldest_seconds` + `ensemble_pg_leaked_txn_rows{site}` | PoolLeak |
| Event-loop starvation → livez/readyz 25–30 s timeouts, CPU 116–134% | manual `/proc` thread deltas, wedge-capture forensics | loop-lag histogram (exported from existing lag probe) + `ensemble_thread_cpu_seconds{thread_group}` + `ensemble_wedge_capture_total{reason}` + textfile `ensemble_process_cpu_seconds{name}` (catches bash spins the in-daemon metrics cannot see) | LoopStarved, BashSpin, external liveness (CR-1) |
| Checkpoint reads 73–99 s (14 GB table, TOAST 10× bloat, shared_buffers 160 MB) | manual pg table-size math + query watching | `ensemble_checkpoint_op_seconds{op}` (extended buckets) + `ensemble_checkpoint_op_bytes{op}` + `ensemble_checkpoint_blobs_table_bytes` + `ensemble_checkpoint_versions_per_thread` (p95 gauge) | CheckpointSlow, CheckpointBloat |
| Readiness false-degraded (P2 deadline 5 s < 17 s reads) while daemon healthy | reading `psycopg_pool.PoolTimeout` tracebacks in journals | `ensemble_readiness_probe_total{component,outcome}` + `ensemble_readiness_grace_active` (Pure PromQL FalseDegraded) | FalseDegraded |
| `state.json` O(n²) bash journal walk (promote + launcher both spun at 99.9%) | noticing a bash process eating a core with no fds | textfile `ensemble_journal_file_bytes`, `ensemble_journal_history_entries`, `ensemble_journal_op_seconds` + `ensemble_upgrade_phase_seconds` | JournalWalkRisk |
| Mission-reconciliation sweep hot loop (every ~8 s vs 300 s configured) | counting log lines per minute | `ensemble_sweep_cycle_seconds{lane}` + `ensemble_sweep_rows_total{lane,outcome}` | SweepHotLoop |
| Lanes parked in `waiting_children` forever (lost wakes after restarts) | recursive parent_id tree walks | `ensemble_waiting_children_count` + `ensemble_waiting_children_max_age_seconds` + `ensemble_wake_turns_total{outcome}` | LostWake |
| Pool checkout timeouts / exhaustion | grep `PoolTimeout` | `ensemble_db_pool_connections{pool,state}` + `ensemble_db_pool_checkout_seconds{pool}` + `ensemble_db_pool_requests_errors_total{pool}` (superset, CR-4) | PoolExhausted |
| Wedged daemon cannot export own /metrics (event-loop starvation) | livez/readyz timed out 25–30 s Oct-26/28; no external liveness alert | out-of-process witnesses: `up==0`/`absent()` external liveness + `scrape_duration_seconds` alert + textfile `*.prom` dir + node_exporter + postgres_exporter + wedge-capture artifacts (threads.csv/lag.json/readyz.json) | ExternalLivenessDown, ScrapeDurationHigh (CR-1) |

**Leak story (P1 pool poisoned).** Recurring `idle in transaction` connections from sites `begin` / `instances_list` / `parent_lookup` / `queue_count` / `other` (fixed label set — no unbounded query strings). Census runs on a dedicated non-pooled connection at ≥15 s cadence (never through P1/P2 — a probe consuming pool slots is the failure mode it detects). Per-site attribution requires a fragment→site map + `application_name='ensemble-metrics-census'` + db_capture.py discipline + `statement_timeout` (MA-13: leaked-txn site attribution asserted not derived; daemon connections carry empty application_name).

## 2. Scope & deploy target

**In scope (v2).** Incident-driven first set: daemon runtime, DB pools + Postgres census, checkpoint store, readiness, jobs, sweeps, instances, MCP health, LLM tokens/cost, compaction pressure, tool-call volume, lifecycle counters, shell-side textfile (journal/upgrade), host + Postgres exporters, dashboards + alerts + 2-week burn-in. ~30 metric families total, enumerated by name in the catalog (metric-catalog.md).

**Out of scope (explicit).** Distributed tracing/APM; per-request HTTP histograms; log aggregation pipelines; synthetic canaries; high-cardinality per-instance breakdowns beyond fixed vocabularies (`site`/`lane`/`server`/status/tier); skill-metrics lane `/api/skills/{id}/metrics` (m5 — already has its own surface, `/api/skills/{id}/metrics`; re-surfacing in /metrics would duplicate and double-count); VictoriaMetrics/Mimir compat claim (m9 — dropped unless tested; target is Prometheus+Grafana); k8s deploy (CR-5).

**Deploy target (RATIFIED, decision 1).** Host binaries / VM compose. k8s is explicitly out-of-scope and is a separate project — the v1 reference to `infranstructure/k8s/` (misspelled and absent in both trees) is withdrawn; no k8s manifests ship in this plan. Retention: 30 d raw / 90 d 5 m-downsampled (plenty at this scale).

**Cardinality budget (MA-10).** Daemon-origin ≤2 k series (holds: ~450–600). Stock exporters (node_exporter, postgres_exporter) are measured separately and are NOT counted against the daemon budget; an AC asserts the split.

## 3. Architecture

**Separate localhost-bound /metrics listener (decision 3 + CR-2).** `/metrics` is NOT served on the main app public port (9797). The main app binds 0.0.0.0 (config.py:566), AuthConfig.enabled defaults False (config.py:3524), CORS falls back to `*` (api.py:4693) — landing `/metrics` there would expose an unauthenticated, unbounded scrape surface (CR-2). Instead: a dedicated localhost-bound listener (127.0.0.1 only) on a separate port (managed key `limits.metrics_listener_port`, default 9798), gated by the existing origin-guard surface (`maintenance_origin_guard.py`) for browser access and by bind-address for programmatic scrape. Explicit never-exported list enforced at render time (§7).

**Out-of-process witnesses (CR-1).** The daemon cannot export its own /metrics while wedged (event-loop starvation). Witnesses that survive a wedge: (a) external liveness `up==0`/`absent()` + `scrape_duration_seconds` alert on the scrape itself; (b) textfile `*.prom` dir (node_exporter textfile collector) written by an out-of-process writer; (c) node_exporter (host CPU/mem/disk/load); (d) postgres_exporter (PG rollups, table sizes, WAL); (e) wedge-capture artifacts (threads.csv, lag.json, readyz.json) under the wedge-capture store. These are the witnesses the on-loop /metrics cannot be.

**Prometheus-vs-resource_monitor split (decision 4).** Prometheus is authoritative for alerting series (scrape + alert). resource_monitor PG tier (`resource_daemon_bucket` 30/60/3600 s rollups, `resource_instance_sample`, `resource_pid_map`, `resource_limit_policy`) is authoritative for per-instance API views (`/api/resources/*`). `/metrics` is a view over the same services, not a second implementation: samplers are shared, the render path only formats. Per-instance attribution beyond fixed vocabularies stays in the PG tier (MA-10 cardinality).

**Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly).** The v1 Gap-5/A-8 `no-prometheus_client` decision is REAFFIRMED, not silently superseded. The renderer stays dependency-free (no `prometheus_client`, no OTel). Exposition is **Prometheus classic text exposition format** (NOT OpenMetrics — MA-16 corrects the v1 mislabel). The existing `checkpoint_metrics.py` registry (Counter + Histogram + `render_metrics()`) generalizes into the central registry (CR-3); no new dependency is introduced. The supersession is named here so reviewers see it is a deliberate reaffirmation, not an oversight.

**Off-loop discipline (non-negotiable).** Metric collection never adds event-loop work: background threads, atomic counters, samplers off-loop (existing lag probe + wedge watchdog are the templates). One `/proc` dialect (reuse `resource_monitor/proc_readers.py`, sampler.py already imports it). Per-request ENSEMBLE_METRICS_ENABLED read (MA-1).

**Textfile collector (R18, genuinely new).** House bash scripts (launcher/promote/stage) append/rewrite `*.prom` files in `$INSTALL_DIR/data/metrics/` (node_exporter textfile collector). Catches the bash spins in-daemon metrics cannot see. Journal metrics via python twin `daemon/tools/upgrade_journal.py` (decision 2 — never touch `scripts/upgrade/`).

## 4. Phases

### Coupling map (phase × phase)

| | P0 | P1a | P1b | P1c | P2 |
|---|---|---|---|---|---|
| P0 | — | tight (registry) | tight (registry+census) | tight (registry) | loose (textfile dir) |
| P1a | tight | — | loose (resource spine) | independent | independent |
| P1b | tight | loose | — | loose (pools/jobs) | loose (blob query) |
| P1c | tight | independent | loose | — | independent |
| P2 | loose | independent | loose | independent | — |

P0 is on the critical path for all; P1a/b/c can run in parallel after P0 lands (loose coupling between them).

### P0 — Foundation (owner: devops; ~2–3 days)

Goals: listener + security; kill-switch; registry generalization; census discipline; resource-spine health gate; vocabularies enumerated.

Metric families added (names only): none new — P0 builds the plumbing. (Existing: `message_api_checkpoint_list_total`, `message_api_saver_op_latency_seconds`.)

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Stand up localhost-bound /metrics listener (127.0.0.1:<port>) | none | Port binds loopback only; main port 9797 returns 404 for /metrics |
| 2 | Wire origin guard (maintenance_origin_guard.py pattern) for browser access | 1 | Browser scrape from non-loopback origin returns 403 origin_not_trusted |
| 3 | Implement ENSEMBLE_METRICS_ENABLED per-request read, default OFF | 1 | Kill-switch flips without restart; /metrics returns empty-shape 200 when off |
| 4 | Generalize checkpoint_metrics.py registry into central registry (Counter/Gauge/Histogram + render); extend checkpoint buckets top ≥120 s | none | render() emits all registered families; 73–99 s read lands in finite bucket |
| 5 | Register managed keys (§5) with bounds mirroring pydantic Fields | 4 | len(MANAGED_KEYS) census updated; bounds validated at write time |
| 6 | Enumerate vocabularies fully (MA-12) | none | Doc lists 10 instance statuses, 7 sweep lanes, MCP DB-row-bounded server list, version-label policy |
| 7 | Census discipline: dedicated non-pooled connection, ≥15 s cadence, application_name='ensemble-metrics-census', fragment→site map, statement_timeout | none | Integration test asserts non-pooled connection usage |
| 8 | Resource-spine health gate: "4 tables accepting rows" (depends on WS-A flusher.py:770 fix) | none | resource_daemon_bucket, resource_instance_sample, resource_pid_map, resource_limit_policy all accept rows |

Exit criteria:
- `/metrics` reachable only on 127.0.0.1:<port>; main port 9797 returns 404 for /metrics; origin guard wired.
- `ENSEMBLE_METRICS_ENABLED` per-request read, default OFF; kill-switch flips without restart.
- `checkpoint_metrics.py` registry generalized; checkpoint buckets extended top ≥120 s (CR-3); one-measurement-one-name; slow-op ledger is CheckpointSlow source.
- Vocabularies enumerated fully (MA-12).
- "4 tables accepting rows" exit criterion for resource spine (MA-3).
- Census discipline in place (MA-13).
- Managed keys registered (§5) with bounds mirroring pydantic Fields (R9).

Test plan: unit tests for registry render + bucket extension; integration test for listener bind (asserts 127.0.0.1 only); kill-switch flip test; resource-spine "4 tables accepting rows" gate test; census connection test (asserts non-pooled).

Owner role: devops (infra/listener) + backend (registry/census).

Risks (P0):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | WS-A flusher fix slips → P0 exit blocked | High | Medium | P0 can ship without resource spine; gate tracked separately; P1a resource_* families gated on WS-A |
| 2 | Listener port collision on 9798 | Low | Low | Managed key allows override; document default |
| 3 | Bucket extension changes CheckpointSlow threshold semantics | Medium | Low | Re-baseline alert threshold in P1b; unit test pins finite bucket for 73–99 s |

### P1a — Runtime & resource spine (owner: backend; ~3–4 days)

Goals: export the runtime metrics that catch wedges, LLM, compaction, tools, lifecycle.

Metric families added (names only — canonical names per metric-catalog.md §1; a name that is not catalogued is marked "proposed — not catalogued" and is tracked in decisions.md §H H-7): `ensemble_loop_lag_seconds` (exported from existing lag probe — no duplicate), `ensemble_thread_cpu_seconds{thread_group}`, `ensemble_wedge_capture_total{reason}`, `ensemble_process_cpu_seconds{name}` (textfile; catalog F22 assigns this family to P2 — decisions.md §H H-8), `ensemble_llm_tokens_total{direction}`, `ensemble_llm_request_total{outcome}` + `ensemble_llm_request_seconds` (the latency histogram carries no outcome label — catalog F15), `ensemble_llm_stream_stalls_total`, `ensemble_compaction_fired_total{rung}`, `ensemble_compaction_occupancy_ratio` (at compaction.py:1128 hooks), `ensemble_tool_calls_total{category}`, `ensemble_turn_duration_seconds` (catalog F20 — the per-turn seam; a per-tool latency histogram is NOT shipped), `ensemble_gii_throttle_active` (proposed — not catalogued), `ensemble_resource_spine_tables_accepting_rows{table}` + `ensemble_resource_spine_rows_written_total{table}` (catalog F0.3 — the spine health gate; per-instance attribution stays in the resource_monitor PG tier), `ensemble_wake_turns_total{outcome}`, `ensemble_instance_lifecycle_total{event}` (catalog F13 — 7 events, incl. revive-on-send_message).

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Export lag probe as `ensemble_loop_lag_seconds` histogram (reuse, no duplicate) | P0-4 | p50/p95 gauges + documented dependency when wedge-capture off (MA-4) |
| 2 | Thread-CPU sampler reuse + bucketing into ~8 groups (MA-2) | P0-4 | sampler.py reused; one /proc dialect; groups bounded |
| 3 | Wedge-capture counter hook + textfile process CPU | P0-1 | counter increments on trigger; textfile written out-of-process |
| 4 | LLM tokens/cost + real outcome enum + stall count at watchdog abort site (MA-8) | P0-4 | truncation-undercount caveat documented (graph.py:3876–3910) |
| 5 | Compaction pressure hooks at compaction.py:1128 (m6) | P0-4 | compaction_fired_total{rung} fires on L1/L2/L3 |
| 6 | Tool-call perf via message_tap.py seam (m7) | P0-4 | volume + latency per tool; not graph internals |
| 7 | Lifecycle counters (spawn/revive/pause/fan-in) (m8) | P0-4 | revive-on-send_message captured (lost-wakes class) |

Exit criteria: every family renders; thread-CPU reuses sampler.py (one /proc dialect); LLM tokens reconcile with truncation-undercount caveat; compaction hooks fire on the 3 rungs; tool-call seam is message_tap.py; cardinality census passes.

Test plan: fault-injection fixtures per family; CPU-delta self-cost via /api/resources/self-cost; thread-group bucketing unit test.

Owner role: backend.

Risks (P1a):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | LLM token undercount on length-cap (graph.py:3760–3812) | Medium | High | Document caveat in metric help; reconcile against usage_metadata where available |
| 2 | Thread-CPU bucketing churns cardinality | Medium | Medium | Fixed ~8 groups; unit test pins group set |
| 3 | Compaction hook overhead on hot path | Low | Medium | Atomic counter increment only; no I/O on loop |

### P1b — Persistence, pools & checkpoint (owner: backend; ~3–4 days)

Goals: DB pools, Postgres census, checkpoint store, readiness, maintenance, snapshots.

Metric families added (names only — canonical names per metric-catalog.md §1; a name that is not catalogued is marked "proposed — not catalogued" and is tracked in decisions.md §H H-7): `ensemble_db_pool_native{pool,quantity}` + `ensemble_db_pool_checked_out{pool}` + `ensemble_db_pool_waiting_clients{pool}` (derived vs native kept apart — catalog F4/F5), `ensemble_db_pool_checkout_seconds{pool}` (deferred — not catalogued; the checkout seam is instrumented in a follow-up, see the CR-4 note below), `ensemble_db_pool_requests_errors_total{pool,kind}` (superset, CR-4), `ensemble_pg_backends{state}`, `ensemble_pg_idle_in_txn_oldest_seconds`, `ensemble_pg_leaked_txn{site}`, `ensemble_checkpoint_op_seconds{op}`, `ensemble_checkpoint_op_bytes{op}`, `ensemble_checkpoint_blobs_table_bytes`, `ensemble_checkpoint_versions_per_thread` (p95 gauge), `ensemble_readiness_probe_total{component,outcome}`, `ensemble_readiness_grace_active`, `ensemble_maintenance_runs_total{operation,outcome}` (proposed — not catalogued), `ensemble_snapshot_usage_total{outcome}` (proposed — not catalogued; source /api/snapshots/metrics), `ensemble_db_pool_status{pool}` (proposed — not catalogued; do NOT ship beside `ensemble_db_pool_native` — catalog F4 is the single pool-status family).

Note (CR-4): `ensemble_db_pool_checkout_timeouts_total{pool}` is NOT shipped — psycopg_pool 3.3.1 exposes no native timeout counter (`requests_errors` is a superset counting every waiting exception plus TooManyRequests). PoolExhausted is rebased on `ensemble_db_pool_requests_errors_total`. Instrumenting daemon's own checkout call sites is deferred to a follow-up; the superset + documented caveat ships in v2.

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | DB pool gauges + checkout histogram (wrap both pools) | P0-4 | pool=manager/checkpoint; state vocabulary fixed |
| 2 | Postgres census on dedicated connection (MA-13) | P0-7 | idle-in-txn oldest age + per-site leak rows |
| 3 | Checkpoint op latency/bytes histograms (extended buckets) | P0-4 | 73–99 s read finite; slow-op ledger feeds CheckpointSlow |
| 4 | Blob table size + versions-per-thread p95 gauge (R7) | P0-4 | maintenance.py VACUUM_FULL_TABLE_BYTES_SQL source |
| 5 | Readiness probe outcome counters preserving 11-class taxonomy (MA-5) | P0-4 | labels = 11 failure classes; grace on /readyz; no re-probe |
| 6 | Maintenance + snapshot usage counters | P0-4 | /api/snapshots/metrics source; maintenance_runs TTL note (no TTL today) |

Exit criteria: checkpoint buckets extended top ≥120 s (CR-3); CheckpointSlow rebased on bucket-extended histogram + slow-op ledger; census on dedicated connection; readiness labels preserve 11-class taxonomy (readiness.py:56–79) with grace on /readyz (no re-probe).

Test plan: fault-injection for CheckpointSlow (simulate 73–99 s read → alert fires; with old buckets it would not); census dedicated-connection test; readiness FalseDegraded pure PromQL test.

Owner role: backend + devops (PG).

Risks (P1b):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Census query amplifies pool pressure if mis-wired | High | Low | Dedicated connection enforced by integration test; statement_timeout |
| 2 | Readiness labels drift from 11-class taxonomy | Medium | Medium | Import constants from readiness.py; unit test pins class set |
| 3 | Blob table size query cost on 14 GB table | Low | Medium | pg_relation_size (cheap) not full scan; 15 s cadence |

### P1c — Jobs, instances, sweeps & MCP (owner: backend; ~2–3 days)

Goals: job queue, workers, heartbeat, waiting-children, instance status, sweeps, MCP health.

Metric families added (names only — canonical names per metric-catalog.md §1; a name that is not catalogued is marked "proposed — not catalogued" and is tracked in decisions.md §H H-7): `ensemble_job_queue_depth{admission_state}` (reads admission_state), `ensemble_job_claim_latency_seconds`, `ensemble_workers_busy{pool}`, `ensemble_workers_total{pool}`, `ensemble_task_heartbeat_age_seconds` (max), `ensemble_waiting_children_count`, `ensemble_waiting_children_max_age_seconds`, `ensemble_instance_status_count{status}` (10 values), `ensemble_sweep_cycle_seconds{lane}`, `ensemble_sweep_rows_total{lane,outcome}`, `ensemble_mcp_server_state{server,state}`, `ensemble_mcp_failures_total{server,kind}`, `ensemble_message_queue_depth` (proposed — not catalogued), `ensemble_message_queue_oldest_age_seconds` (proposed — not catalogued), `ensemble_skill_usage_duration_seconds` (proposed — not catalogued; durable exception, skill_usage_records — distinct from the m5 skill-metrics non-goal lane), `ensemble_turn_events_total{source}` + `ensemble_instance_lifecycle_total{event}` (catalog F20/F13 — replaces the greenfield `ensemble_event_total{kind}` vocabulary; see decisions.md §H H-11).

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Job queue depth reading admission_state (m3) | P0-4 | queued/active/dead/done vocabulary matches admission_state |
| 2 | Claim-latency instrumented at the seam (m3) | P0-4 | histogram populated; seam named in code comment |
| 3 | Worker utilization from worker_pool.py get_stats (R10) | P0-4 | busy/total gauges per pool |
| 4 | Heartbeat age (max) with cadence/source documented (m4) | P0-4 | max age gauge; source + cadence in metric help |
| 5 | Waiting-children count + max age + wake-turns counter (m1 producer named) | P0-4 | Carrier-Threading: producer computed once, carried on ProcessingContext, threaded kw-only |
| 6 | Instance status counts (10 values) | P0-4 | vocabulary pinned by unit test |
| 7 | Sweep cycle duration + rows with actual interval (MA-6) | P0-4 | rate(..._cycle_seconds_count[5m]) basis |
| 8 | MCP server state + failure counters (DB-row-bounded) | P0-4 | server list from DB rows, not config |

Exit criteria: queue depth reads admission_state (m3); claim-latency instrumented at the seam (m3 — name it); heartbeat-age has cadence/source documented (m4); wake counters have a named producer per Carrier-Threading rule (m1); sweep hot-loop alert via rate(..._cycle_seconds_count[5m]) (MA-6); MCP server list DB-row-bounded.

Test plan: fault-injection for SweepHotLoop (8 s vs 300 s); LostWake (parked >30 min); incident-replay per row.

Owner role: backend.

Risks (P1c):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | m1 producer not identifiable → counter never increments | High | Medium | Name canonical producer in P1c task; Carrier-Threading discipline check in review |
| 2 | Wake counters double-count on revive | Medium | Medium | Idempotency on (instance, wake_id); unit test |
| 3 | MCP server list unbounded if DB rows grow | Low | Low | DB-row-bounded by construction; cap documented |

### P2 — Exporters, textfile, journal twin, dashboards & alerts burn-in (owner: devops; ~3–4 days)

Goals: node_exporter + postgres_exporter + textfile wiring + journal python twin + dashboards + alerts + 2-week burn-in.

Metric families added (names only): textfile `ensemble_journal_file_bytes`, `ensemble_journal_history_entries`, `ensemble_journal_op_seconds` + `ensemble_journal_ops_total{op}` (the op label lives on the counter — catalog F21), `ensemble_upgrade_phase_seconds{phase}`, `ensemble_upgrade_release_current{version}`; host CPU%/disk await/util/load/mem (node_exporter); PG rollups/table sizes/WAL/autovacuum/dead tuples (postgres_exporter).

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Wire textfile collector into launcher/promote/stage (out-of-process, crash-safe) | P0-1 | *.prom files written; node_exporter textfile collector reads them |
| 2 | Journal metrics via python twin daemon/tools/upgrade_journal.py (decision 2) | P0-1 | Never touches scripts/upgrade/; ties to releases/state.json (MA-14) |
| 3 | Deploy node_exporter + postgres_exporter | P0-1 | Both scrape targets healthy |
| 4 | Dashboards: Overview / DB+pools / Checkpoint+storage | P0-1 | All families graphed |
| 5 | Deploy all alert rules with for: clauses | P0-4 | promtool check rules passes |
| 6 | 2-week burn-in review scheduled | P0-1 | Review ticket created |

Exit criteria: textfile collector wired into launcher/promote/stage (out-of-process, crash-safe); journal metrics via python twin daemon/tools/upgrade_journal.py (decision 2; never touch scripts/upgrade/); dashboards (Overview / DB+pools / Checkpoint+storage); all alert rules deployed with for: clauses; 2-week burn-in review scheduled; journal metrics tied to releases/state.json (MA-14 — incident was entry-count O(n²), not bytes; example exposition corrected).

Test plan: textfile freshness test; journal op duration test; alert rule unit tests (promtool check rules); incident-replay within 5 min per row.

Owner role: devops.

Risks (P2):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Textfile staleness if writer crashes | Medium | Medium | node_exporter textfile mtime alert; writer heartbeat |
| 2 | postgres_exporter query load on PG | Low | Medium | Custom queries only for top-N; default queries otherwise |
| 3 | Alert fatigue during burn-in | Medium | High | Start with page-worthy rules only; tune during 2-week review |

## 5. Kill-switch & managed keys

**Kill-switch (MA-1).** `ENSEMBLE_METRICS_ENABLED` is read per-request (not restart-gated, unlike the managed-key-only gate at settings_service.py:190). Default OFF. Layered under the managed-keys registry (R9) so the managed-keys surface (DB override > env > yaml > default) can also flip it, but the per-request env read is the authoritative off-switch for emergencies (no restart). When OFF: `/metrics` returns empty-shape 200 (§6), samplers stop ticking, no new series.

**New managed keys (R9 MANAGED_KEYS registry pattern; bounds mirror pydantic Fields).**

| Key | Type | Default | Bounds | Restart? | Notes |
|---|---|---|---|---|---|
| `limits.metrics_enabled` | bool | false | — | No (per-request) | Kill-switch; mirrors ENSEMBLE_METRICS_ENABLED. |
| `limits.metrics_listener_port` | int | 9798 | ge=1024, le=65535 | Yes | Localhost-bound listener port. |
| `limits.metrics_scrape_interval_hint_s` | int | 15 | ge=5, le=300 | No | Advisory; Prometheus scrape_interval wins. |
| `limits.metrics_census_interval_s` | int | 15 | ge=15 | Yes | Dedicated PG census cadence. |
| `limits.metrics_sampler_interval_s` | int | 10 | ge=1, le=60 | Yes | /proc sampler cadence. |
| `limits.metrics_textfile_dir` | str | `$INSTALL_DIR/data/metrics` | — | Yes | Textfile collector dir. |
| `limits.metrics_cardinality_cap` | int | 2000 | ge=100, le=10000 | No | Soft cap; warn on breach. |

All entries follow the registry's value_type + ge/le/gt validation truth (Ruling 1); managed keys require a registry entry + nothing else.

## 6. Degradation contract

**Empty-shape 200, never 503 (MA-15).** A 503 reads as "target DOWN" to Prometheus and fires `absent()`/`up==0` alerts spuriously. When any component is unavailable, `/metrics` returns HTTP 200 with an empty (or partial) body and a comment line `# <family> unavailable: <reason>`. Scrape stays cheap when everything is dark (kill-switch off → empty 200 in <1 ms).

**Which families go dark under which condition.**

| Condition | Dark families | Still live |
|---|---|---|
| `ENSEMBLE_METRICS_ENABLED=false` | All daemon-origin families | Textfile `*.prom` (out-of-process), node_exporter, postgres_exporter, external liveness |
| Wedge-capture disabled (`ENSEMBLE_WEDGE_CAPTURE_ENABLED=false`) | `ensemble_wedge_capture_total`, lag-probe-derived `ensemble_loop_lag_seconds` (MA-4: lag probe is dark when wedge-capture off — export computed p50/p95 as gauges + document the dependency) | thread-CPU sampler (independent), resource_monitor spine |
| WS-A spine broken (flusher 42804 not fixed) | `ensemble_resource_*` per-instance attribution; "4 tables accepting rows" gate fails → P0 exit blocked | All other families; resource_monitor HTTP views degrade to empty shape |
| PG census connection unavailable | `ensemble_pg_*` | Everything else (census is isolated, non-pooled) |
| Kill-switch flipped mid-scrape | Current scrape completes with whatever was cached; next scrape empty | Textfile/exporters unaffected |

## 7. Never-exported list

Explicit categories never exported (enforced at render time):
- No free-form SQL strings (census `site` is a fixed 5-value enum; query text never appears).
- No secrets / tokens / credentials / API keys (including KMS handles, bearer tokens, OAuth secrets).
- No unbounded strings: no instance IDs as labels, no thread IDs, no user content, no file paths, no error messages beyond a bounded reason-code enum.
- No high-cardinality per-instance breakdowns beyond fixed vocabularies (`site`, `lane`, `server`, status 10-value, sweep lane 7-value, tier, pool, op, outcome, component).
- No PII (user IDs, emails, IPs).
- No raw checkpoint bytes / blob content / message bodies.
- No per-request data (per-request HTTP histograms are a non-goal).

## 8. Alert table

Every rule with valid PromQL, each mapped to the incident it replays. for: clauses stated explicitly.

| Alert | Expr | for: | Incident replayed |
|---|---|---|---|
| ExternalLivenessDown (CR-1) | `up{job="ensemble"} == 0 or absent(up{job="ensemble"})` | 1m | Wedged daemon cannot export own /metrics (Oct-26/28 livez/readyz 25–30 s timeouts) |
| ScrapeDurationHigh (CR-1) | `scrape_duration_seconds{job="ensemble"} > 5` | 2m | Slow scrape = event-loop starvation precursor |
| PoolLeak | `ensemble_pg_idle_in_txn_oldest_seconds > 60` | 5m | Oct-26/28 leaked `idle in transaction` conns |
| PoolExhausted (CR-4) | `increase(ensemble_db_pool_requests_errors_total{pool="checkpoint"}[5m]) > 0` | 0m | PoolTimeouts (rebased on requests_errors superset; no native timeout counter exists in psycopg 3.3.1; the manager pool has no requests_errors equivalent, so pool="manager" renders no series) |
| LoopStarved (MA-4) | `ensemble_loop_lag_seconds{stat="p95"} > 2 and ensemble_loop_lag_samples > 150` | 5m | 116–134% CPU wedges (rolling-window quantile gauge — no `_bucket` series exists) |
| CheckpointSlow (CR-3) | `histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket{op="aget"}[10m])) > 5 and increase(ensemble_checkpoint_op_seconds_count[10m]) > 0` | 10m | 73–99 s reads (rebased on bucket-extended histogram + slow-op ledger; old 10 s top bucket collapsed p95 to 10.0 and fired spuriously forever) |
| CheckpointBloat | `ensemble_checkpoint_blobs_table_bytes > 8e9` | 30m | 14 GB table / TOAST 10× bloat |
| JournalWalkRisk | `ensemble_journal_file_bytes > 200000 or ensemble_journal_history_entries > 1000` | 5m | O(n²) bash journal walk (99.9% spins) |
| SweepHotLoop (MA-6) | `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2` | 5m | 8 s vs 300 s configured hot loop |
| LostWake | `ensemble_waiting_children_max_age_seconds > 1800` | 5m | Parked lanes (lost wakes after restarts) |
| BashSpin (MA-7) | `ensemble_process_cpu_seconds{name=~"promote_sh|stage_sh|launcher"} > 0.9` | 5m | 99.9% bash spins (direct comparison on the textfile per-tick gauge; spinning bash cannot self-report — external sampler owner = textfile collector writer) |
| FalseDegraded (MA-5) | `rate(ensemble_readiness_probe_total{outcome="timeout"}[5m]) > 0.1 and ensemble_pg_backends{state="active"} < 3` | 5m | P2 deadline false-negative (pure PromQL; grace already on /readyz — do not re-probe) |
| WedgeCaptureFired | `increase(ensemble_wedge_capture_total[5m]) > 0` | 0m | Sustained CPU / loop-lag / concentration wedge |

for-for clauses are mandatory on every rule; 0m rules are event-style (fire on first occurrence).

## 9. Reuse map (never rebuild what these cover)

- R1 `checkpoint_metrics.py:273-315` registry + renderer (generalize into central registry; CR-3).
- R2 `checkpoint_perf.py:190,198` + slow-op ledger (128-deque RAM) — CheckpointSlow source; one-measurement-one-name.
- R3 `lag_probe.py` (300-sample rolling window, p95 at :185) — export computed quantiles as gauges; dark when wedge-capture off (MA-4).
- R4 `wedge_capture/sampler.py` (reuses `resource_monitor/proc_readers.py`; one /proc dialect) — post-process bucketing for `thread_cpu_seconds{thread_group}` (MA-2).
- R5 `resource_monitor` spine (sampler 30 s, ring 48 h, flusher 60 s, retention sweep; 4 tables) — post-WS-A (flusher.py:770 fix) (MA-3).
- R6 `/api/resources/*` + `maintenance` + `/api/snapshots/metrics` surfaces — /metrics is a view over same services.
- R7 `maintenance.py` VACUUM_FULL_TABLE_BYTES_SQL (~:533) — blob table size source for `ensemble_checkpoint_blobs_table_bytes`.
- R8 `readiness.py` 11-class taxonomy (:56-79) + grace on /readyz — preserve labels, no re-probe (MA-5).
- R9 MANAGED_KEYS registry (settings_service.py:190) — bounds pattern for new metrics keys.
- R10 `worker_pool.py` `get_stats` (:1518, incl. wakeup_efficiency) — worker utilization source.
- R11 `db_capture.py` census discipline — `application_name` + fragment→site map + statement_timeout (MA-13).
- R12 collector.py A2 vocab (pool_checked_out derived, requests_errors superset) — pool honesty (m2).
- R13 `ens_db_tools.py:1157` pool status seam — `ensemble_db_pool_status`.
- R14 MCP MachineStore roster — MCP server state source.
- R15 sweep interval registry (300 s / 90 s) — SweepHotLoop baseline.
- R16 `MAINTENANCE_ENDPOINTS_ENABLED` gating pattern — kill-switch analog.
- R17 `message_tap.py` per-turn seam — tool-call perf source (m7, not graph internals).
- R18 textfile collector (genuinely new) — R18 is the only greenfield piece; everything else reuses.

## 10. Acceptance criteria

Testable (MA-9/MA-10):
1. Incident replay: for each row in §1, the corresponding alert fires within 5 min using only these metrics, verified against Oct-26/28 timelines (fault-injection fixtures per alert — MA-9).
2. Self-cost: daemon CPU delta ≤1% measured via `/api/resources/self-cost` under a 15 s scrape (MA-9).
3. Cardinality census: daemon-origin ≤2 k series; stock exporters (node_exporter, postgres_exporter) measured separately and not counted against the daemon budget (MA-10).
4. Scrape cost: `/metrics` returns in <50 ms on a healthy daemon; empty-shape 200 in <1 ms when dark (MA-15).
5. No pool pressure: census on dedicated non-pooled connection; no new pool slots consumed by scraping.
6. No loop-lag regression: p95 loop lag unchanged under scrape load (≤ baseline +10%).
7. Incident-replay within 5 min per incident row (§1 table).
8. All alert rules pass `promtool check rules`; for: clauses present on every rule.
9. Bucket extension verified: a 73–99 s checkpoint read produces a finite bucket (not +Inf) and CheckpointSlow fires only when p95 > 5 s sustained (CR-3).
10. PoolExhausted verified against `requests_errors` superset with documented caveat (CR-4).

## 11. Non-goals

Distributed tracing / APM; per-request HTTP histograms; log aggregation pipelines (journald stays as-is); synthetic canaries; high-cardinality per-instance breakdowns beyond fixed vocabularies; skill-metrics lane `/api/skills/{id}/metrics` (m5 — already has its own surface; re-surfacing would duplicate and double-count); VictoriaMetrics/Mimir compat claim (m9 — dropped unless tested; target is Prometheus+Grafana); k8s deploy (CR-5 — separate project).

## 12. Open questions

- OQ-1: Should `ensemble_loop_lag_seconds` remain a histogram or be exported as gauges (p50/p95) given the lag probe's 300-sample rolling window is not a Prometheus histogram? (MA-4 — recommend gauges + document dependency; decide in P0.)
- OQ-2: Does the textfile dir live under `$INSTALL_DIR/data/metrics/` or a dedicated mount? (P0 decision; affects node_exporter textfile collector config.)
- OQ-3: Version label policy — include `version` label on every series or only on `ensemble_upgrade_release_current`? (MA-12 — recommend only on the release gauge to avoid churn; decide in P0.)
- OQ-4: `ensemble_event_total{kind}` requires a new event-kind vocabulary; confirm with the events subsystem owner before P1c. (Greenfield.)

(All other review items are resolved by the ratified decisions and the phase exit criteria above.)
