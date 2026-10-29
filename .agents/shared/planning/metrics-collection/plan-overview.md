# Plan Overview: v2 Metrics Collection

Date: 2026-10-29
Author: planner[v2] via plan-creation worker
Status: Draft / Ready for Review
Branch: feature/metrics-collection @ 9bbef71c1 (worktree agents-ensemble-wt-metrics-v2)

## 1. Objective & incident motivation

**Objective.** Ship an incident-driven metrics surface for the agents-ensemble daemon that (a) replays every Oct-26/28 incident as a firing alert within 5 minutes using only these metrics, (b) is scrapeable by stock Prometheus + Grafana with zero custom parsers, and (c) adds <1% host CPU with no pool pressure or loop-lag regression — all while the daemon is wedged (event-loop starvation IS the motivating incident).

**Corrected "Why" (per checkpoint-bench-20261029.md).** The v1 narrative attributed the 17 s checkpoint INSERTs to raw write throughput on Postgres-on-Longhorn. The bench REJECTED that: writes are fast (28 ms/full PUT, 78 PUT/s concurrent, max 254 ms over 6 min sustained; fsync cost ≈2.4 ms). The 17 s event was a stall/contention artifact (WAL-checkpoint window — checkpointer averages 2.3 min write time per checkpoint, 15% forced; or pool contention), not storage throughput. **The real bottleneck is the read path**: cold 1–2.5 MB blob reads cost 1–2.4 s each (scatter of ~2 KB TOAST chunks over Longhorn) because (a) shared_buffers=160 MB vs a 13 GB TOAST, (b) TOAST ~10× bloated with dead chunks from pruned-but-never-reclaimed versions, (c) every checkpoint read is a fresh cache-miss storm. Hence the story is **read latency + TOAST bloat + shared_buffers**, not write throughput. Metric design follows: `ensemble_checkpoint_op_seconds` (CR-3: buckets extended top ≥120 s so 73–99 s reads do not collapse into +Inf) + `ensemble_checkpoint_blobs_table_bytes` (CheckpointBloat alert) + slow-op ledger (existing) as the CheckpointSlow source. Write-side "slow" is now expected to be rare; the metric exists to catch the stall/contention artifact, not throughput.

**Write-side restatement (A6/A7).** The checkpoint write path (`aput`/`aput_writes`) runs through `daemon/checkpoint_adapter.py:377-379` and bypasses `time_saver_op`, so `ensemble_checkpoint_op_seconds{op="aput"}` cannot fire today. The declared `op` enum is narrowed to the observable set (`aget`) until the adapter's existing charge seam (`_charge_checkpoint_read`, `:371-374`) is extended to `aput`/`aput_writes` in P1b (own risk row — it touches the write path under stress). Write-side "slow" is expected to be rare; the metric catches the stall/contention artifact, not throughput (registry wired to collectors, exposition unwired — see metric-catalog.md CR-3 record correction).

**Incident → metric table (corrected).**

| Incident (real) | By-hand diagnosis | Metric that replaces it | Alert |
|---|---|---|---|
| P1 pool poisoned by leaked `idle in transaction` conns (Oct 26+28, recurring) | manual `pg_stat_activity` census + fingerprinting | `ensemble_pg_backends{state}` + `ensemble_pg_idle_in_txn_oldest_seconds` + `ensemble_pg_leaked_txn{site}` | PoolLeak |
| Event-loop starvation → livez/readyz 25–30 s timeouts, CPU 116–134% | manual `/proc` thread deltas, wedge-capture forensics | loop-lag histogram (exported from existing lag probe) + `ensemble_thread_cpu_seconds{thread_group}` + `ensemble_wedge_capture_total{reason}` + textfile `ensemble_process_cpu_seconds{name}` (catches bash spins the in-daemon metrics cannot see) | LoopStarved, BashSpin, external liveness (CR-1) |
| Checkpoint reads 73–99 s (14 GB table, TOAST 10× bloat, shared_buffers 160 MB) | manual pg table-size math + query watching | `ensemble_checkpoint_op_seconds{op}` (extended buckets) + `ensemble_checkpoint_op_bytes{op}` + `ensemble_checkpoint_blobs_table_bytes` + `ensemble_checkpoint_versions_per_thread` (p95 gauge) | CheckpointSlow, CheckpointBloat |
| Readiness false-degraded (P2 deadline 5 s < 17 s reads) while daemon healthy | reading `psycopg_pool.PoolTimeout` tracebacks in journals | `ensemble_readiness_probe_total{component,outcome}` + `ensemble_readiness_grace_active` (Pure PromQL FalseDegraded) | FalseDegraded |
| `state.json` O(n²) bash journal walk (promote + launcher both spun at 99.9%) | noticing a bash process eating a core with no fds | textfile `ensemble_journal_file_bytes`, `ensemble_journal_history_entries`, `ensemble_journal_op_seconds` + `ensemble_upgrade_phase_seconds` | JournalWalkRisk |
| Mission-reconciliation sweep hot loop (every ~8 s vs 300 s configured) | counting log lines per minute | `ensemble_sweep_cycle_seconds{lane}` + `ensemble_sweep_rows_total{lane,outcome}` | SweepHotLoop |
| Lanes parked in `waiting_children` forever (lost wakes after restarts) | recursive parent_id tree walks | `ensemble_waiting_children_count` + `ensemble_waiting_children_max_age_seconds` + `ensemble_wake_turns_total{outcome}` | LostWake |
| Pool checkout timeouts / exhaustion | grep `PoolTimeout` | `ensemble_db_pool_native{pool,quantity}` + `ensemble_db_pool_checked_out{pool}` + `ensemble_db_pool_waiting_clients{pool}` + `ensemble_db_pool_requests_errors_total{pool,kind}` (superset, CR-4 — no native checkout-timeout counter exists) | PoolExhausted |
| Wedged daemon cannot export own /metrics (event-loop starvation) | livez/readyz timed out 25–30 s Oct-26/28; no external liveness alert | out-of-process witnesses: `up==0`/`absent()` external liveness + `scrape_duration_seconds` alert + textfile `*.prom` dir + node_exporter + postgres_exporter + wedge-capture artifacts (threads.csv/lag.json/readyz.json) | ExternalLivenessDown, ScrapeDurationHigh (CR-1) |

**Leak story (P1 pool poisoned).** Recurring `idle in transaction` connections attributed to a **closed 6-value site set** — `instance_read` / `instance_parent_lookup` / `job_queue_read` / `checkpoint_saver` / `agent_db_tool` / `other` (the `other` residual is never dropped, so the census total always equals the sum — metric-catalog.md §8.9/§9.2; no unbounded query strings). Census runs on a dedicated non-pooled connection at ≥15 s cadence (never through P1/P2 — a probe consuming pool slots is the failure mode it detects). Per-site attribution requires a fragment→site map + `application_name='ensemble-metrics-census'` + db_capture.py discipline + `statement_timeout` (MA-13: leaked-txn site attribution asserted not derived; daemon connections carry empty application_name).

## 2. Scope & deploy target

**In scope (v2).** Incident-driven first set: daemon runtime, DB pools + Postgres census, checkpoint store, readiness, jobs, sweeps, instances, MCP health, LLM tokens/cost, compaction pressure, tool-call volume, lifecycle counters, shell-side textfile (journal/upgrade), host + Postgres exporters, dashboards + alerts + 2-week burn-in. ~30 metric families total, enumerated by name in the catalog (metric-catalog.md).

**Out of scope (explicit).** Distributed tracing/APM; per-request HTTP histograms; log aggregation pipelines; synthetic canaries; high-cardinality per-instance breakdowns beyond fixed vocabularies (`site`/`lane`/`server`/status/tier); skill-metrics lane `/api/skills/{id}/metrics` (m5 — already has its own surface, `/api/skills/{id}/metrics`; re-surfacing in /metrics would duplicate and double-count); VictoriaMetrics/Mimir compat claim (m9 — dropped unless tested; target is Prometheus+Grafana); k8s deploy (CR-5).

**Deploy target (RATIFIED, decision 1).** Host binaries / VM compose. k8s is explicitly out-of-scope and is a separate project — the v1 reference to `infranstructure/k8s/` (misspelled and absent in both trees) is withdrawn; no k8s manifests ship in this plan. Retention: Prometheus 30 d raw / 90 d 5 m-downsampled; a **Prometheus process restart preserves history (WAL replay)** — only **storage loss** destroys it. PG tiers (resource_monitor) are separate: 48 h raw / 14 d minute-rollup / 180 d hour-rollup (see §3 PG retention ladder). **The PROD host is macOS (WS-A correction, 2026-10-29):** the prod daemon is a macOS host binary — this is not a dev-box aside but the deploy target itself. Consequence stated plainly: Darwin has no `/proc` and no `/sys/fs/cgroup`, so before the WS-A merge (`feature/resource-monitor-flusher-fix@53547b915`, which lands before P0 executes) the resource_monitor producer was dead in prod (§3 platform note; catalog §13) and spine-derived families are dark until it merges.

**Cardinality budget (MA-10).** Daemon-origin ≤2 k series (holds: ~450–600). Stock exporters (node_exporter, postgres_exporter) are measured separately and are NOT counted against the daemon budget; an AC asserts the split.

## 3. Architecture

**Separate localhost-bound /metrics listener (decision 3 + CR-2).** `/metrics` is NOT served on the main app public port (9797). The main app binds 0.0.0.0 (config.py:566), AuthConfig.enabled defaults False (config.py:3524), CORS falls back to `*` (api.py:4693) — landing `/metrics` there would expose an unauthenticated, unbounded scrape surface (CR-2). Instead: a dedicated localhost-bound listener (127.0.0.1 only) on a separate port (managed key `limits.metrics_listener_port`, default 9798), gated by the existing origin-guard surface (`maintenance_origin_guard.py`) for browser access and by bind-address for programmatic scrape. Explicit never-exported list enforced at render time (§7).

**Out-of-process witnesses (CR-1).** The daemon cannot export its own /metrics while wedged (event-loop starvation). Witnesses that survive a wedge: (a) external liveness `up==0`/`absent()` + `scrape_duration_seconds` alert on the scrape itself; (b) textfile `*.prom` dir (node_exporter textfile collector) written by an out-of-process writer; (c) node_exporter (host CPU/mem/disk/load); (d) postgres_exporter (PG rollups, table sizes, WAL); (e) wedge-capture artifacts (threads.csv, lag.json, readyz.json) under the wedge-capture store. These are the witnesses the on-loop /metrics cannot be.

**Prometheus-vs-resource_monitor split (decision 4).** Prometheus is authoritative for alerting series (scrape + alert). resource_monitor PG tier (`resource_daemon_bucket` 30/60/3600 s rollups, `resource_instance_sample`, `resource_pid_map`, `resource_limit_policy`) is authoritative for per-instance API views (`/api/resources/*`). `/metrics` is a view over the same services, not a second implementation: samplers are shared, the render path only formats. Per-instance attribution beyond fixed vocabularies stays in the PG tier (MA-10 cardinality).

**Dependency-free renderer (decision 5 + MA-16, REAFFIRMED loudly).** The v1 Gap-5/A-8 `no-prometheus_client` decision is REAFFIRMED, not silently superseded. The renderer stays dependency-free (no `prometheus_client`, no OTel). Exposition is **Prometheus classic text exposition format** (NOT OpenMetrics — MA-16 corrects the v1 mislabel). The existing `checkpoint_metrics.py` registry (Counter + Histogram + `render_metrics()`) generalizes into the central registry (CR-3); no new dependency is introduced. The supersession is named here so reviewers see it is a deliberate reaffirmation, not an oversight.

**Registry locking contract (A9, CR-1 mitigation).** Per-metric locks only — **no global registry lock** (one wedged writer must not stall all families). Writers update a family's own state under its own short lock and atomically swap a published pointer (plain assignment, atomic under the GIL); render uses **non-blocking try-acquire** (bounded wait) and on contention serves the **last published snapshot** plus a `# <family> unavailable: lock_contended` comment line. Bucket counts are stored **non-cumulative** and accumulated at render, so `Histogram.observe` is O(1). This is what makes `/metrics` survive a wedged writer instead of hanging.

**Off-loop discipline (restated, A28).** The blanket "never adds event-loop work" claim is literally false — every increment is loop or executor-thread work. The accurate rule: **no blocking work, no I/O, no logging, and no per-chunk work on the event loop**, plus **≤1 `time.perf_counter()` read per observation window**. Background threads, atomic counters, samplers stay off-loop (existing lag probe + wedge watchdog are the templates); one `/proc` dialect (reuse `resource_monitor/proc_readers.py`, sampler.py already imports it); per-request `ENSEMBLE_METRICS_ENABLED` read (MA-1). Metric increments inside `message_tap` must sit **within the tap's existing `try/except`** (`daemon/services/message_tap.py:308-309`) so a metric error cannot produce per-turn WARNING noise. Per-turn cost budget: ≈4 µs added work per tool-round turn (8 counter incs + 2 histogram observes) against a multi-second turn — **<0.01%**; the tap's own DB write exceeds any metric cost by 3–4 orders of magnitude.

**Textfile collector (R18, genuinely new).** House bash scripts (launcher/promote/stage) append/rewrite `*.prom` files in `$INSTALL_DIR/data/metrics/` (node_exporter textfile collector). Catches the bash spins in-daemon metrics cannot see. Journal metrics via python twin `daemon/tools/upgrade_journal.py` (decision 2 — never touch `scripts/upgrade/`). **V-8 external sampler (do NOT let bash scripts write their own `*.prom`):** the textfile is produced by an out-of-process external sampler — owner = node_exporter textfile-collector writer (devops-owned), mechanism = per 10 s tick runs `ps -o %cpu=,comm=` (Darwin) / reads `/proc/<pid>/stat` (Linux) for the named processes (`promote.sh`, `stage.sh`, `launcher`) and rewrites `$INSTALL_DIR/data/metrics/ensemble_textfile.prom` with the per-tick delta gauge `ensemble_process_cpu_seconds{name="…"}` plus the freshness gauge `ensemble_textfile_last_write_timestamp_seconds`; the pinned cadence is 10 s (alert math assumes it); spin→stale interaction: when the textfile goes stale (>120 s) while a spin is in progress, `TextfileStale` fires and the operator is alerted that the BashSpin signal is no longer trustworthy. See plan §8 "V-8 — BashSpin external sampler design" for the full contract.

**Listener thread/loop topology (A12, CR-1 mitigation).** The listener owns a **dedicated daemon thread** running stdlib `http.server.ThreadingHTTPServer` bound to `127.0.0.1` on its own port, started from the FastAPI lifespan (Option A). A shared-main-event-loop listener is **REJECTED** — it inherits the CR-1 failure (the scrape dies exactly when the daemon wedges). **Render-never-blocks:** per-metric locks only (no global registry lock); render uses non-blocking try-acquire (bounded wait, e.g. 50 ms) and on contention serves the **last published snapshot** plus a `# <family> unavailable: lock_contended` comment line; writers update a family's own state under its own short lock and atomically swap a published pointer (plain assignment, atomic under the GIL) — copy-on-publish. **GIL split:** an **I/O-bound wedge survives** (I/O releases the GIL) but a **CPU-spin wedge does not** (the spin holds the GIL continuously) — so CR-1's out-of-process witnesses (node_exporter textfile, `up==0`/`absent()`, `scrape_duration_seconds`) remain **MANDATORY**, not optional. CR-3 reuse story: the registry is **wired to collectors, exposition unwired** — only `render_metrics()` is unwired in production (`checkpoint_perf.py:28-30` imports the singletons, observes `:190`/`:198`, increments `:233`).

**Boot/teardown lifecycle (A13).** Boot placement, inside the FastAPI **lifespan**: start after `resource_monitor` (`daemon/api.py:2241-2288`) and after `llm_stream_watchdog` (`daemon/api.py:2212-2226`); a bind failure degrades the listener to **disabled + logged**, mirroring `daemon/api.py:2282-2287` (never abort boot — decision 3). Teardown order (R-m2 — aligned to the house detached-join precedent at `daemon/api.py:3411-3433` and `daemon/services/file_change_monitor.py:180-196`): stop event → `server.shutdown()` → **detached join (no blocking join)** → close, **after** `resource_monitor` stop (`daemon/api.py:3411-3433`) and wedge-capture stop (`daemon/api.py:3435-3464`); the listener thread is `daemon=True` so a wedged join is already harmless and **must never block the event loop** (the join is detached, never `join(timeout=…)` on the loop thread). The earlier `join(timeout=5)` wording is superseded by this detached-join precedent; a wedged join cannot hang shutdown.

**Storage-split boundary rule (A18).** Per-instance attribution = **PG-only via `/api/resources/*`**; every daemon-origin `/metrics` family is **aggregate**; **no daemon-origin family carries a per-instance label**. Enforced in catalog §8 as a cardinality rule (not just stated).

**PG retention ladder (A19).** resource_monitor PG tiers: raw **48 h** (`retention_raw_minutes=2880`), minute-rollup **14 d** (`retention_minute_hours=336`), hour-rollup **180 d** (`retention_hour_days=180`) — `daemon/config.py:818-888`. Interplay rule: any quantity visible on both sides is governed by **Prometheus for alerting (30 d+)** and by **PG for per-instance API views**; questions beyond Prometheus's 90 d window are answered from PG's 180 d hour-rollup tier. Prometheus side: 30 d raw / 90 d 5 m-downsampled.

**Two-sampler consistency (A20).** The wedge-capture sampler and the resource-monitor sampler read `/proc` independently — one shared reader module (`resource_monitor/proc_readers.py`), two independent loops. They are **not** required to agree byte-for-byte; consistency is eventual within one resource-monitor sample interval. Stated so a future reader does not "fix" a non-bug.

**Platform support summary (WS-A correction, 2026-10-29; full matrix = catalog §13).** The prod host is macOS (§2), so the `/proc` dialect question is a **prod** concern, not a dev-box edge case. Per family: exposition/listener/registry (F0.1/F0.2/F0.4), loop lag (F1), DB pools/census (F4–F6), checkpoint (F7–F9), jobs/workers/sweeps/instances (F10–F13), LLM (F14–F16), MCP/gateway (F17/F18), compaction (F19), turn/tool seam (F20), journal twin (F21) and the host exporters (F22) are **cross-platform**. **Linux-only fields**: PSS (`smaps_rollup`), all cgroup memory/cpu fields (no cgroup v2 on Darwin), true private-anon (Darwin degrades `anon_kb=rss_kb`), `swap_kb` (unwired today on every platform), and — **permanently, by decided non-goal** — all per-child T3 `/proc` values (catalog §13.4). The spine producer itself (F0.3) was **dead in prod until the WS-A merge**: `proc_readers` returned `None` on every Darwin tick → sampler skipped every tick → `flush {batches:1139, bytes:0, errors:0}` ("produced nothing", not "write failed") while the spawn-path-fed `resource_pid_map` climbed and masked the outage. `feature/resource-monitor-flusher-fix@53547b915` restores it with a Darwin ps-based reader (daemon-level fields: rss/vsize via `ps`, threads via `ps -M`, fds via `/dev/fd`, CPU + hw-peak via `getrusage`) + corrected skipped-sample counters (canonical — F0.3 rule). F2 thread-CPU **stays dark on macOS even after the merge** (the wedge sampler is `/proc`-only and WS-A does not touch it). Consumer-visible shape changes are pinned in catalog §13.3 (`/api/resources/live`/`history` payload on Darwin; `coverage_pct` becomes rss-denominated).

**Freeze rules (A27).** Import the listener on the **lifespan path** (not `daemon/api.py` module top level) so the PyInstaller analyzer pulls it. Register aliases at **registry-construction time** (not lazily) so a cold start still emits the deprecated names for the remainder of their one-release window.

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

**Coupling note (A26):** P2 **alert authoring** depends on the referenced families' completion (e.g. `SweepHotLoop` depends on the `lane` vocabulary pinned in P1a/b/c); **dashboards may be drafted earlier**. The coupling map marks P0↔P2 as "loose" for the textfile dir only; the alert dependency is the tight part.

### P0 — Foundation (owner: devops; ~2–3 days)

Goals: listener + security; kill-switch; registry generalization; census discipline; resource-spine health gate; vocabularies enumerated.

Metric families added (names only): **F0.1–F0.4 foundation families** (P0 ships the foundation metrics — listener/exposition, canonical registry, resource-spine health gate, vocabulary freeze marker). (Existing: `message_api_checkpoint_list_total`, `message_api_saver_op_latency_seconds`.)

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Stand up localhost-bound /metrics listener (127.0.0.1:<port>) | none | Port binds loopback only; main port 9797 returns 404 for /metrics. **Boot placement (A13):** inside the FastAPI lifespan, after `resource_monitor` (`daemon/api.py:2241-2288`) and `llm_stream_watchdog` (`:2212-2226`); bind failure → disabled + logged (`:2282-2287`), never abort boot. Teardown (R-m2): stop event → `shutdown()` → **detached join (no blocking join; `daemon=True` thread dies with the process)** after resource_monitor stop (`:3411-3433`) and wedge-capture stop (`:3435-3464`) — aligned to the house detached-join precedent (`daemon/api.py:3411-3433`, `daemon/services/file_change_monitor.py:180-196`); never `join(timeout=…)` on the event loop. |
| 2 | Wire origin guard (maintenance_origin_guard.py pattern) for browser access | 1 | Browser scrape from non-loopback origin returns 403 origin_not_trusted |
| 3 | Implement ENSEMBLE_METRICS_ENABLED per-request read, default OFF | 1 | Kill-switch flips without restart; /metrics returns empty-shape 200 when off. **Semantics (A14):** truthy set = `{1, true, yes}` case-insensitive; everything else OFF (fail closed — `ture`/`on`/`enabled`/`0`/empty → OFF). Layering: env explicit-OFF always wins; env UNSET defers to the managed-key chain (unset + managed key on ⇒ ON; unset + default ⇒ OFF); explicit env ON overrides the managed-key default. One boot log line when env unset while managed key on. Per-request env read stays (~50–100 ns, no syscall; no TTL cache). |
| 4 | Generalize checkpoint_metrics.py registry into central registry (Counter/Gauge/Histogram + render); extend checkpoint buckets top ≥120 s | none | render() emits all registered families; 73–99 s read lands in finite bucket. **F0.2 (canonical registry metric):** `ensemble_metrics_registry_measurements` (gauge, counts registered measurements) is emitted from the generalized registry (`checkpoint_metrics.py:304`); alias mechanics per catalog §0.3. **R-m3 (cap rejected counter):** `ensemble_metrics_cap_rejected_total` (counter, `{metric}`-labeled, folded into F0.2) is emitted by the hard cap's atomic observe-time rejection. |
| 5 | Register managed keys (§5) with bounds mirroring pydantic Fields | 4 | len(MANAGED_KEYS) census updated; bounds validated at write time |
| 6 | Enumerate vocabularies fully (MA-12) | none | Doc lists 10 instance statuses, 7 sweep lanes, MCP DB-row-bounded server list, version-label policy. **P-1 (V-4 companion):** the tool-category vocabulary is **frozen from the V-4 generated artifact** (catalog §8.6 — CI/test-generated JSON artifact + drift check + `other`-fold), **never from a hand list**; the P0 freeze either (a) freezes from the artifact's sorted list (becomes the frozen label set for that release) or (b) defers the freeze and ships with the `other`-fold as the closure (bump `ensemble_metrics_vocabulary_version`). Reference: catalog §8.6 freeze rule + §8 rule A1 (derived from code, never hand-listed). |
| 7 | Census discipline: dedicated non-pooled connection, ≥15 s cadence, application_name='ensemble-metrics-census', fragment→site map, statement_timeout | none | Integration test asserts non-pooled connection usage |
| 8 | Resource-spine health gate: "4 tables accepting rows" (depends on the WS-A Darwin-producer fix — **`feature/resource-monitor-flusher-fix@53547b915`** merging to latest before P0 executes; if not merged, this gate BLOCKS — see the P0-spine split below and catalog F0.3/§11 C35) | none | resource_daemon_bucket, resource_instance_sample, resource_pid_map, resource_limit_policy all accept rows. **F0.3 (both series):** `ensemble_resource_spine_tables_accepting_rows{table}` (gauge, table ∈ the 4 spine tables) + `ensemble_resource_spine_rows_written_total{table}` (counter, table ∈ the same 4). |
| 9 | Name worker threads at construction (`worker_pool.py:253`: `name=f"worker-{lane}-{worker_id}"`) so the thread-CPU family's groups are honest from first render | none | the thread-CPU family's worker-pool/chat-worker-pool groups no longer fold into `other` (A23; moves cost out of a P1a rework cycle) |
| 10 | Census execution-home: dedicated daemon thread (`CensusSampler`, `daemon=True`, `time.monotonic` tick) — not the listener thread, not the event loop (A23; catalog §9.1) | none | Census survives a wedge; statement_timeout 5 s / connect_timeout 3 s (catalog §9.1) |
| 11 | Instrument exposition listener metrics (F0.1 — V-5 orphan sweep) | P0-1 | Emit `ensemble_metrics_up` (gauge, no labels), `ensemble_metrics_scrape_requests_total{outcome}` (counter, outcome ∈ ok/error), and `ensemble_metrics_exposition_seconds` (histogram, buckets §8.11) from the listener seam (`daemon/services/metrics_listener.py`); kill-switch OFF ⇒ `ensemble_metrics_up=0`, scrape counter still increments `outcome="ok"`, exposition histogram records the empty render. Owner: developer (implement) + devops (port/scrape). |

Exit criteria:
- `/metrics` reachable only on 127.0.0.1:<port>; main port 9797 returns 404 for /metrics; origin guard wired.
- `ENSEMBLE_METRICS_ENABLED` per-request read, default OFF; kill-switch flips without restart.
- `checkpoint_metrics.py` registry generalized; checkpoint buckets extended top ≥120 s (CR-3); one-measurement-one-name; slow-op ledger is CheckpointSlow source.
- Vocabularies enumerated fully (MA-12).
- "4 tables accepting rows" exit criterion for resource spine (MA-3).
- Census discipline in place (MA-13).
- **P-3 gate conditions (mechanics — all APPLIED, anchor cited):** (a) **detached-join teardown** per the house precedent (`daemon/api.py:3411-3433`, `daemon/services/file_change_monitor.py:180-196`) — R-m2, listener thread `daemon=True`, join detached, never blocks the event loop; (b) **atomic hard cap + `ensemble_metrics_cap_rejected_total`** — R-m3, observe-time rejection atomic under the metric's own lock (check-then-increment, no TOCTOU), rejected counts folded into F0.2; (c) **bounded publisher cadence** — R-m5, dirty-flag + re-publish at most once per N seconds (or publish-on-scrape-request with bounded staleness), writers never pay O(series) per increment. Gate reads "applied, or accepted deviation with rationale" — all three are applied (no deviation).
- **Versioned vocabulary-amendment procedure (A24):** label NAMES + closed enums pinned to
  current code at P0; any vocabulary change bumps `ensemble_metrics_vocabulary_version`; a
  bump that changes a literal label VALUE appearing in shipped PromQL is a **breaking alert
  change** and needs a stated compatibility rule (catalog §8 rule + F0.4).

**P0 exit-criteria split (A22).** **P0-core** (ships independently): listener, registry,
kill-switch, census discipline, vocabularies, managed keys. **P0-spine** (the "4 tables
accepting rows" gate): owned by the parallel `rm-flusher-fix` workstream (WS-A) and tracked as a
**separate gate** — P0 risk 1's "P0 can ship without the resource spine" is reconciled with the
exit criterion (no contradiction). The gate criteria itself is unchanged:
`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`,
`resource_limit_policy` all accept rows. **WS-A merge dependency (WS-A correction, 2026-10-29 — A3):**
the producer precondition for this gate is the merge of **`feature/resource-monitor-flusher-fix`**
at commit **`53547b915`** ("restore the dark resource-telemetry tier (the macOS /proc gap)" — the
Darwin ps-based reader + corrected skipped-sample counters), which merges to latest **before P0
executes**. The gate verifies against the merged tree. **If that branch is not merged, the
4-table gate BLOCKS — it does not silently pass and it does not downgrade** to a warning or a
deferred status; P0-spine stays open until the gate goes green on a tree carrying `53547b915`.
(Why the producer, not the flusher: the refuted "flusher 42804 / window rollback" framing is
corrected in catalog §11 C35 — prod's `estimated` column is `boolean NOT NULL`, the bind is
correct, the amplifier was already fixed by `7be1b40bf` + `69cea24ea`; the tier was dark because
the macOS prod host had no `/proc`, so the sampler produced nothing while
`resource_pid_map` climbed from the spawn path and masked the outage.)

Test plan: unit tests for registry render + bucket extension; integration test for listener bind (asserts 127.0.0.1 only); kill-switch flip test; resource-spine "4 tables accepting rows" gate test; census connection test (asserts non-pooled). **Plus (A16):** origin-guard 403 (handler-level request through the guard; non-loopback origin → 403 `origin_not_trusted`); bind-failure degradation (inject a failing bind → listener disabled + logged, boot continues); wedged-writer simulation (acquire a metric's lock in the test thread, then render → scrape still returns within 100 ms, family skipped with `lock_contended`); kill-switch typo parsing (`ENSEMBLE_METRICS_ENABLED=ture` → OFF). **Harness:** injectable socket factory (ephemeral port 0 or no socket) so no test needs a real port; `render_metrics()` stays pure/port-free.

Owner role: devops (infra/listener) + backend (registry/census).

Risks (P0):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | WS-A (Darwin-producer fix) not merged by the time P0-spine executes → the 4-table gate BLOCKS | High | Low (fix already committed at `53547b915`; merges to latest before P0 executes) | P0-core can ship without the resource spine (A22 split); **P0-spine itself cannot** — the gate blocks, it does not silently pass or downgrade; P1a `resource_*` families gated on the WS-A merge. The blocked link is the **producer** (macOS `/proc` gap), not the flusher — the 42804 framing is refuted (catalog §11 C35) |
| 2 | Listener port collision on 9798 | Low | Low | Managed key allows override; document default |
| 3 | Bucket extension changes CheckpointSlow threshold semantics | Medium | Low | Re-baseline alert threshold in P1b; unit test pins finite bucket for 73–99 s |

### P1a — Runtime, wedge, pools, PostgreSQL census (owner: backend; ~3–4 days)

Goals: export the runtime metrics that catch wedges, DB pools, PostgreSQL census.

Metric families added (names only — canonical names per metric-catalog.md §1; an uncatalogued name is **not shipped** in v2 — see decisions.md §H H-7): `ensemble_loop_lag_seconds` (exported from existing lag probe — no duplicate), `ensemble_thread_cpu_seconds{thread_group}`, `ensemble_wedge_capture_total{reason}`, `ensemble_db_pool_native{pool,quantity}` + `ensemble_db_pool_checked_out{pool}` + `ensemble_db_pool_waiting_clients{pool}` + `ensemble_db_pool_requests_errors_total{pool,kind}` (superset, CR-4), `ensemble_pg_backends{state}`, `ensemble_pg_idle_in_txn_oldest_seconds`, `ensemble_pg_leaked_txn{site}`.

Note (CR-4): the checkout-timeout counter is **retired** (see decisions.md §B CR-4) — psycopg_pool 3.3.1 exposes no native timeout counter (`requests_errors` is a superset counting every waiting exception plus TooManyRequests). PoolExhausted is rebased on `ensemble_db_pool_requests_errors_total`. Instrumenting daemon's own checkout call sites is deferred to a follow-up; the superset + documented caveat ships in v2.


Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Export lag probe as `ensemble_loop_lag_seconds` histogram (reuse, no duplicate) | P0-4 | p50/p95 gauges + documented dependency when wedge-capture off (MA-4) |
| 2 | Thread-CPU sampler reuse + bucketing into **11 groups** (MA-2; catalog F2 / §8.2) | P0-4 | sampler.py reused; one /proc dialect; groups bounded |
| 3 | Wedge-capture counter hook | P0-1 | counter increments on trigger; the shell textfile writer ships in **P2** (external sampler owner) |
| 4 | DB pool gauges — native quantities + derived checked-out/waiting (both pools) | P0-4 | pool=manager/checkpoint; native-quantity vocabulary fixed; the checkout-latency histogram is retired (see decisions.md §B CR-4) |
| 5 | Postgres census on dedicated connection (MA-13) | P0-7 | idle-in-txn oldest age + per-site leak rows |

Exit criteria: every family renders; thread-CPU reuses sampler.py (one /proc dialect); census on dedicated connection; cardinality census passes.

Test plan: fault-injection fixtures per family; CPU-delta self-cost via /api/resources/self-cost; thread-group bucketing unit test; census dedicated-connection test.

Owner role: backend.

Risks (P1a):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Thread-CPU bucketing churns cardinality | Medium | Medium | Fixed **11 groups** (catalog F2 / §8.2); unit test pins group set |
| 2 | Census query amplifies pool pressure if mis-wired | High | Low | Dedicated connection enforced by integration test; statement_timeout |

### P1b — Checkpoint, jobs, sweeps, instances, lifecycle (owner: backend; ~3–4 days)

Goals: checkpoint store, jobs, sweeps, instances, lifecycle, readiness, maintenance, snapshots.

Metric families added (names only — canonical names per metric-catalog.md §1; an uncatalogued name is **not shipped** in v2 — see decisions.md §H H-7): `ensemble_checkpoint_op_seconds{op}` + `ensemble_checkpoint_op_bytes{op}` + `ensemble_checkpoint_slow_ops_total` + `ensemble_checkpoint_list_total`, `ensemble_checkpoint_blobs_table_bytes` + `ensemble_checkpoint_table_bytes` + `ensemble_checkpoint_versions_per_thread` + `ensemble_checkpoint_prune_runs_total`, `ensemble_readiness_probe_total{component,outcome}` + `ensemble_readiness_grace_active` + `ensemble_readiness_failure_streak` + `ensemble_readiness_component_state`, `ensemble_job_queue_depth{admission_state}` + `ensemble_job_claim_latency_seconds` + `ensemble_dead_letter_items` + `ensemble_job_queue_items_created_total`, `ensemble_workers_total{pool}` + `ensemble_workers_busy{pool}` + `ensemble_worker_pool_wakeup_efficiency` + `ensemble_worker_pool_events_total` + `ensemble_task_heartbeat_age_seconds` + `ensemble_task_heartbeat_cadence_seconds`, `ensemble_sweep_cycle_seconds{lane}` + `ensemble_sweep_cycles_total` + `ensemble_sweep_errors_total` + `ensemble_sweep_rows_total`, `ensemble_instance_status_count{status}` + `ensemble_instance_liveness_count` + `ensemble_waiting_children_count` + `ensemble_waiting_children_max_age_seconds` + `ensemble_wake_turns_total{outcome}` + `ensemble_instance_lifecycle_total{event}`. (The minimal textfile writer for the F22 family ships here as a task-level early-ship per A25; the family itself maps to P2 — see the intra-family phased-rollout convention in catalog §7.)

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Checkpoint op latency/bytes histograms (extended buckets) | P0-4 | 73–99 s read finite; slow-op ledger feeds CheckpointSlow |
| 2 | Blob table size + versions-per-thread p95 gauge (R7) | P0-4 | maintenance.py VACUUM_FULL_TABLE_BYTES_SQL source |
| 3 | Readiness probe outcome counters preserving 11-class taxonomy (MA-5) | P0-4 | labels = 11 failure classes; grace on /readyz; no re-probe |
| 4 | Maintenance + snapshot usage counters | P0-4 | /api/snapshots/metrics source; maintenance_runs TTL note (no TTL today) |
| 5 | Job queue depth reading admission_state (m3) | P0-4 | queued/active/dead/done vocabulary matches admission_state |
| 6 | Claim-latency instrumented at the seam (m3) | P0-4 | histogram populated; seam named in code comment |
| 7 | Worker utilization from worker_pool.py get_stats (R10) | P0-4 | busy/total gauges per pool |
| 8 | Heartbeat age (max) with cadence/source documented (m4) | P0-4 | max age gauge; source + cadence in metric help |
| 9 | Waiting-children count + max age + wake-turns counter (m1 producer named) | P0-4 | Carrier-Threading, **two producers** (A3): (a) the claim path `daemon/services/worker_pool.py:1381-1386`, (b) the episode seam `daemon/graph.py:7319` `_detect_fresh_episode_reset` — each computed **once**, each carried on `ProcessingContext`, threaded keyword-only. **Consumer (A4):** a **live turn-end consumer increments the counter; the renderer only reads the accumulated counter** (the per-message carrier dies with the message, so a scrape-time renderer cannot read it) |
| 10 | Instance status counts (10 values) | P0-4 | vocabulary pinned by unit test |
| 11 | Sweep cycle duration + rows with actual interval (MA-6) | P0-4 | `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2` basis — the cycle metric is a **gauge**, so no `_count` series exists (catalog F12 / §10.2) |
| 12 | Lifecycle counters (spawn/revive/pause/fan-in) (m8) | P0-4 | revive-on-send_message captured (lost-wakes class) |
| 13 | Minimal textfile writer (`ensemble_process_cpu_seconds`) — only the BashSpin data source (A25) | P0-1 | Textfile gauge present before P2 alert rules land; full exporter work stays in P2 |
| 14 | Instrument the checkpoint **write** path via a **DISTINCT write-side hook** (V-6 — do NOT reuse the read seam) | P0-4 | Add `_charge_checkpoint_write` (its own function, own budget/labels) firing once per successful outer call (same once-per-outer-call contract as `_charge_checkpoint_read`), called from `aput`/`aput_writes` (`checkpoint_adapter.py:377-379`); the read seam `_charge_checkpoint_read` (`:371-374`) is **not** extended to writes (routing write bytes through the read charge would bill them to the READ budget). **Early-return-on-None for writes (decided):** a `None` result on a write path is an **error, not a skip** — the hook does NOT early-return on `None`; instead it records the failure (e.g. via the existing slow-op/error ledger) so a missing write charge is visible. Until it lands, the declared `op` enum stays narrowed to `aget`. Risk row 3 carries the risk. |

Exit criteria: checkpoint buckets extended top ≥120 s (CR-3); CheckpointSlow rebased on bucket-extended histogram + slow-op ledger; readiness labels preserve 11-class taxonomy (readiness.py:64–79; grace subset :83-89, components :94-99) with grace on /readyz (no re-probe); wake counters have **two named producers** per the Carrier-Threading rule (m1; A3 — claim path + episode seam) and a **live turn-end consumer** increments the counter while the renderer only reads the accumulated value (A4); sweep hot-loop alert via `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2` (MA-6 — the gauge form; no `_count` series exists); MCP server list DB-row-bounded.

Test plan: fault-injection for CheckpointSlow (simulate 73–99 s read → alert fires; with old buckets it would not); readiness FalseDegraded pure PromQL test; fault-injection for SweepHotLoop (8 s vs 300 s); LostWake (parked >30 min); incident-replay per row.

Owner role: backend + devops (PG).

Risks (P1b):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Readiness labels drift from 11-class taxonomy | Medium | Medium | Import constants from readiness.py; unit test pins class set |
| 2 | Blob table size query cost on 14 GB table | Low | Medium | pg_relation_size (cheap) not full scan; 15 s cadence |
| 3 | Checkpoint write-path instrumentation (Q1, A6) — extend `checkpoint_adapter.py` `_charge_checkpoint_read` (`:371-374`) to `aput`/`aput_writes` (`:377-379`) in P1b with its own risk row; touches the checkpoint write path under stress during the incidents this plan exists to diagnose | Medium | Medium | Until it lands, the declared `op` enum stays narrowed to the observable set (`aget`); write-side incident mapping restated in §1 (writes are a stall/contention artifact, not throughput); "registry wired to collectors, exposition unwired" (CR-3) |
| 4 | m1 producer not identifiable → counter never increments | High | Medium | Name **both** canonical producers in the P1b task (claim path `worker_pool.py:1381-1386` + episode seam `graph.py:7319`); Carrier-Threading discipline check in review; the counter is incremented by a **live turn-end consumer**, never by the exposition renderer |
| 5 | Wake counters double-count on revive | Medium | Medium | Idempotency on (instance, wake_id); unit test |

### P1c — LLM, MCP, compaction, turn/tool, journal (owner: backend; ~2–3 days)

Goals: LLM tokens/cost, MCP health, compaction pressure, turn/tool seam, journal twin.

Metric families added (names only — canonical names per metric-catalog.md §1; an uncatalogued name is **not shipped** in v2 — see decisions.md §H H-7): `ensemble_llm_tokens_total{direction}` + `ensemble_llm_token_usage_missing_total`, `ensemble_llm_request_total{outcome}` + `ensemble_llm_request_seconds`, `ensemble_llm_stream_stalls_total` + `ensemble_llm_stream_watchdog_sweep_total`, `ensemble_mcp_server_state{server,state}` + `ensemble_mcp_failures_total{server,kind}`, `ensemble_mcps_gateway_sessions` + `ensemble_mcps_gateway_session_total` + `ensemble_mcps_gateway_tombstones` + `ensemble_mcps_gateway_audit_total`, `ensemble_compaction_fired_total{rung}` + `ensemble_compaction_occupancy_ratio` + `ensemble_compaction_precall_estimate_tokens`, `ensemble_turn_events_total{source}` + `ensemble_turn_duration_seconds` + `ensemble_tool_calls_total{category}`, `ensemble_journal_file_bytes` + `ensemble_journal_history_entries` + `ensemble_journal_op_seconds` + `ensemble_journal_ops_total{op}` + `ensemble_upgrade_phase_seconds{phase}` + `ensemble_upgrade_phase_total` + `ensemble_upgrade_release_current{version}` + `ensemble_upgrade_journal_events_total`.

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | LLM tokens/cost + real outcome enum + stall count at watchdog abort site (MA-8) | P0-4 | truncation-undercount caveat documented (graph.py:3876–3910). **Observation site (A2):** hook the **terminal-usage branch `daemon/graph.py:3776-3784`** (the `len(choices) == 0` path that carries the token counts and returns early) — NOT `:3811-3812` (non-terminal, per-chunk) — plus a **per-call dedupe**; rule: **terminal-chunk only, never per-chunk** |
| 2 | LLM request outcome and latency (real 8-value outcome enum) | P0-4 | 8-value enum from the 4-layer ladder; latency histogram carries no outcome label (8 × 14 = 112 series avoided) |
| 3 | LLM stream stalls + watchdog sweep | P0-4 | stall counts at watchdog abort site (`llm_stream_watchdog.py:456`) |
| 4 | Compaction pressure hooks at compaction.py:1128 (m6) | P0-4 | compaction_fired_total{rung} fires on L1/L2/L3 |
| 5 | Tool-call perf via message_tap.py seam (m7) | P0-4 | volume per tool **category** (runtime-derived set + reserved `other`); **not** graph internals. **Counting unit + anchor (A5):** `ensemble_turn_events_total{source}` counts **one `tap_node_return` invocation**; `ensemble_turn_duration_seconds` anchored at **entry-tap timestamp → node-return timestamp** pair — **no per-tool latency histogram ships** (catalog F20) |
| 6 | MCP server state + failure counters (DB-row-bounded) | P0-4 | server list from DB rows, not config |
| 7 | MCPS gateway sessions + audit | P0-4 | sessions/tombstones/audit counters |
| 8 | Journal metrics via python twin daemon/tools/upgrade_journal.py (decision 2) | P0-1 | Never touches `scripts/upgrade/`; ties to releases/state.json (MA-14) |
| 9 | Instrument MCPS gateway metrics (F18 — V-5 orphan sweep) | P0-4 | Emit `ensemble_mcps_gateway_sessions` (gauge), `ensemble_mcps_gateway_session_total{outcome}` (counter, outcome ∈ begin/end/expired/superseded/refused), `ensemble_mcps_gateway_tombstones` (gauge), `ensemble_mcps_gateway_audit_total{outcome}` (counter, outcome ∈ ok/error) from `MachineStore`/`sessions.py`/`tombstones.py`/`audit.py` (`daemon/services/mcps_gateway/`). Gateway disabled (`config.gateway_enabled`, default OFF) ⇒ no series. Owner: developer. |

Exit criteria: LLM tokens reconcile with truncation-undercount caveat documented; compaction hooks fire on the 3 rungs; tool-call seam is message_tap.py; MCP server list DB-row-bounded; MCPS gateway sessions/audit render; journal metrics tied to releases/state.json (MA-14 — incident was entry-count O(n²), not bytes; example exposition corrected).

Test plan: fault-injection fixtures per family; LLM token undercount reconciliation; incident-replay per row; journal op duration test.

Owner role: backend.

Risks (P1c):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | LLM token undercount on length-cap (graph.py:3760–3812; terminal-usage branch :3776-3784 per A2) | Medium | High | Document caveat in metric help; reconcile against usage_metadata where available |
| 2 | Compaction hook overhead on hot path | Low | Medium | Atomic counter increment only; no I/O on loop |
| 3 | MCP server list unbounded if DB rows grow | Low | Low | DB-row-bounded by construction; cap documented |

### P2 — Exporters, textfile, dashboards & alerts burn-in (owner: devops; ~3–4 days)

Goals: node_exporter + postgres_exporter + textfile (full) wiring + dashboards + alerts + 2-week burn-in.

Metric families added (names only): `ensemble_process_cpu_seconds{name}` (full exporter/textfile work) + host CPU%/disk await/util/load/mem (node_exporter); PG rollups/table sizes/WAL/autovacuum/dead tuples (postgres_exporter). (Note: the minimal textfile writer ships in P1b per A25; full exporter/textfile work remains here. Journal metrics are canonically P1c.)

Tasks:
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Wire textfile collector into launcher/promote/stage (out-of-process, crash-safe) | P0-1 | *.prom files written; node_exporter textfile collector reads them. **V-8 (external sampler — bash scripts never write their own `*.prom`):** owner = node_exporter textfile-collector writer (devops); mechanism = per 10 s tick samples `ps -o %cpu=,comm=` (Darwin) / `/proc/<pid>/stat` (Linux) for `promote.sh`/`stage.sh`/`launcher` and rewrites `$INSTALL_DIR/data/metrics/ensemble_textfile.prom` with the per-tick delta gauge `ensemble_process_cpu_seconds{name}` + freshness gauge `ensemble_textfile_last_write_timestamp_seconds`; pinned 10 s cadence; spin→stale interaction per plan §8 V-8 (TextfileStale fires when no write for >120 s while a spin is in progress). |
| 2 | Deploy node_exporter + postgres_exporter | P0-1 | Both scrape targets healthy |
| 3 | Dashboards: Overview / DB+pools / Checkpoint+storage | P0-1 | All families graphed |
| 4 | Deploy all alert rules with for: clauses | P0-4 | promtool check rules passes. **Dependency (A26):** alert authoring depends on the referenced families' completion (e.g. SweepHotLoop depends on the `lane` vocabulary pinned in P1a/b/c); dashboards may be drafted earlier. |
| 5 | 2-week burn-in review scheduled | P0-1 | Review ticket created |

Exit criteria: textfile collector wired into launcher/promote/stage (out-of-process, crash-safe); dashboards (Overview / DB+pools / Checkpoint+storage); all alert rules deployed with for: clauses; 2-week burn-in review scheduled; textfile freshness test passes.

Test plan: textfile freshness test; alert rule unit tests (promtool check rules); incident-replay within 5 min per row.

Owner role: devops.

Risks (P2):
| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Textfile staleness if writer crashes | Medium | Medium | node_exporter textfile mtime alert; writer heartbeat |
| 2 | postgres_exporter query load on PG | Low | Medium | Custom queries only for top-N; default queries otherwise |
| 3 | Alert fatigue during burn-in | Medium | High | Start with page-worthy rules only; tune during 2-week review |
| 4 | Prometheus storage loss is unrecoverable (RK-16, Q2) — single local TSDB; a host loss takes the entire alerting history | Low | Low | Accepted for v2 as a known limitation; remote_write to a durable store is a separate project, explicitly out of scope; record as RK-16 in the risk register |


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
| `limits.metrics_cardinality_cap` | int | 2000 | ge=100, le=10000 | No | **Hard** cap (R-m3): observe beyond the cap is **rejected, not warned**; the observe-time rejection is **atomic under the metric's own lock** (check-then-increment inside the per-metric lock — no TOCTOU window); rejected observations counted in `ensemble_metrics_cap_rejected_total` (folded into F0.2). |

All entries follow the registry's value_type + ge/le/gt validation truth (Ruling 1); managed keys require a registry entry + nothing else.

## 6. Degradation contract

**Empty-shape 200, never 503 (MA-15).** A 503 reads as "target DOWN" to Prometheus and fires `absent()`/`up==0` alerts spuriously. When any component is unavailable, `/metrics` returns HTTP 200 with an empty (or partial) body and a comment line `# <family> unavailable: <reason>`. Scrape stays cheap when everything is dark (kill-switch off → empty 200 in <1 ms).

**Which families go dark under which condition.**

| Condition | Dark families | Still live |
|---|---|---|
| `ENSEMBLE_METRICS_ENABLED=false` | All daemon-origin families | Textfile `*.prom` (out-of-process), node_exporter, postgres_exporter, external liveness |
| Wedge-capture disabled (`ENSEMBLE_WEDGE_CAPTURE_ENABLED=false`) | `ensemble_wedge_capture_total`, lag-probe-derived `ensemble_loop_lag_seconds` (MA-4: lag probe is dark when wedge-capture off — export computed p50/p95 as gauges + document the dependency) | thread-CPU sampler (independent), resource_monitor spine |
| **F3 wedge-capture counters on a non-Linux / macOS host (R-m10, catalog §13)** | **F3 `ensemble_wedge_capture_total` / `ensemble_wedge_capture_write_total`** are **cross-platform at the trigger level** (counters fire), but **wedge capture content is `/proc`-dependent** — on the macOS prod host the wedge sampler is `/proc`-only and WS-A does not touch `wedge_capture/`, so thread-level detail (F2) is dark and captures lack per-thread data (catalog §13.2: "capture *content* on macOS lacks thread-level detail (F2 side); the counters themselves fire"). Wedge capture is effectively **dark on macOS** for thread-attribution purposes; the counters still increment on trigger. | thread-CPU sampler independent; resource_monitor spine (post-WS-A) |
| Resource spine dark — WS-A Darwin-producer fix (`feature/resource-monitor-flusher-fix@53547b915`) not merged | Spine-derived families: F0.3 gate fails (renders 0-producing, **P0-spine blocked**); `/api/resources/live`/`history` empty-ring shapes on prod. The failure mode is a **dead producer** (macOS: no `/proc` → sampler skips every tick → `flush bytes:0, errors:0`), NOT a flusher write failure — the earlier "flusher 42804" framing is refuted (catalog §11 C35) | All other families (the flusher was never the broken link); T3 `resource_pid_map` keeps climbing from the spawn path — this **masks** the outage rather than refuting it |
| PG census connection unavailable | `ensemble_pg_*` | Everything else (census is isolated, non-pooled) |
| Kill-switch flipped mid-scrape | Current scrape completes with whatever was cached; next scrape empty | Textfile/exporters unaffected |
| Env **unset** but managed key `limits.metrics_enabled=true` (A14) | **Not dark** — env-unset is not a statement of intent, so the managed-key chain decides (ON). One boot log line: "`ENSEMBLE_METRICS_ENABLED` unset; deferring to `limits.metrics_enabled`" | Textfile/exporters unaffected |
| A writer holds a metric lock → render cannot try-acquire (A9) | That family only: `# <family> unavailable: lock_contended` + the **last published snapshot** (other families render normally) | Textfile/exporters unaffected |
| **CPU-spin wedge** (GIL held continuously) | The listener thread is starved — it cannot serve; no in-daemon series are exported at all | **Out-of-process only:** external liveness `up==0`/`absent()`, `scrape_duration_seconds`, node_exporter textfile, postgres_exporter, wedge-capture artifacts (CR-1 witnesses are **mandatory**, not optional) |
| **Non-Linux host** (`/proc` absent) — **PROD IS macOS; this row is about prod, not just dev boxes (WS-A correction, catalog §13)** | **Pre-merge:** all spine-derived families (F0.3) + per-instance attribution — `proc_readers` returns `None` on every tick (prod-verified 2026-10-29: 0 rows in `resource_daemon_bucket`/`resource_instance_sample` while `resource_pid_map` climbed from the spawn path). **Post-merge (after `feature/resource-monitor-flusher-fix@53547b915`):** the Darwin ps-based reader restores the producer — daemon-level spine fields go live; still dark on Darwin: PSS, cgroup memory/cpu, true private-anon (`anon_kb=rss_kb` proxy), `swap_kb` (unwired), per-child T3 values (**decided non-goal**, catalog §13.4), and **F2 `ensemble_thread_cpu_seconds` / `ensemble_thread_count`** (the wedge sampler is `/proc`-only; WS-A does not touch it) | The **listener still boots** (pure stdlib socket, no `/proc` dependency) — every cross-platform family renders; post-merge the spine families render with the §13.3 Darwin payload shape (`coverage_pct` rss-denominated) |

## 7. Never-exported list

Explicit categories never exported, enforced **executably (A10)** in three tiers plus defence in
depth:

1. **Registration-time enum pin** — `register_histogram(name, label_keys, allowed_values={...})`
   rejects a wrong label *key* at registration (raises; mirrors `checkpoint_metrics.py:206-211`).
2. **Observe-time reserved bucket** — a wrong label *value* folds to `other` and is counted in
   `ensemble_metrics_invalid_label_total{metric,label}`.
3. **Render-time allowlist predicate** — per-family label-name allowlist + per-label value
   membership (defence in depth; also the hook for this list).

Plus: **hard** cardinality cap (`limits.metrics_cardinality_cap` — hard per R-m3: observe beyond the cap is
rejected, not warned; the rejection is **atomic under the metric's own lock**, check-then-increment
inside the per-metric lock, no TOCTOU window; rejected counts in
`ensemble_metrics_cap_rejected_total`, folded into F0.2),
name regex `^ensemble_[a-z0-9_]+$`, label-value charset gate (no `=`, `,`, `"`, newline), and a
poisoned-registry defence-in-depth test.

Explicit categories never exported (enforced at render time):
- No free-form SQL strings (census `site` is a fixed 6-value enum — 5 named sites + the `other` residual; query text never appears).
- No secrets / tokens / credentials / API keys (including KMS handles, bearer tokens, OAuth secrets).
- No unbounded strings: no instance IDs as labels, no thread IDs, no user content, no file paths, no error messages beyond a bounded reason-code enum.
- No high-cardinality per-instance breakdowns beyond fixed vocabularies (`site` (6-value), `lane` (13-value), `server` (5-value), status 10-value, tier, pool, op, outcome, component).
- No PII (user IDs, emails, IPs).
- No raw checkpoint bytes / blob content / message bodies.
- No per-request data (per-request HTTP histograms are a non-goal).

## 8. Alert table

Every rule with valid PromQL, each mapped to the incident it replays. `for:` clauses stated
explicitly. **Authoritative rule text lives in the fenced ```yaml blocks below** (one block per
rule group, unescaped pipes); the table carries the incident mapping + baseline citations. No
`\|` appears in any PromQL (R-m1).

**Multi-vector rules (V-1).** Every rule that combines two series uses explicit `and on()`
label guards so vector matching actually works — the bare `and` required identical label sets
and silently produced an empty vector (disjoint labels → never fires). Fixed:
LoopStarved, FalseDegraded, CheckpointSlow (and any other multi-vector rule). Each rule ships a
`promtool test rules` evaluation fixture in §10 AC8 (input series + eval time + expected
fire/no-fire), including one fixture that reproduces the pre-fix empty-vector defect and one
proving the fixed expression fires. Syntax-only `promtool check rules` is no longer sufficient.

**Alert count: 14 rules** (13 original + `TextfileStale`). Fixture count: 14 (one per rule,
plus the V-1 defect/repro pair counted inside the per-rule fixtures).

| Alert | Expr (see fenced blocks) | for: | Incident replayed | Baseline |
|---|---|---|---|---|
| ExternalLivenessDown (CR-1) | `up{job="ensemble"} == 0 or absent(up{job="ensemble"})` | 1m | Wedged daemon cannot export own /metrics (Oct-26/28 livez/readyz 25–30 s) | external liveness standard; no code constant — incident evidence (Oct-26/28) |
| ScrapeDurationHigh (CR-1) | `scrape_duration_seconds{job="ensemble"} > 5` | 2m | Slow scrape = event-loop starvation precursor | incident evidence (Oct-26/28 livez 25–30 s timeouts) |
| PoolLeak | `ensemble_pg_idle_in_txn_oldest_seconds > 60` | 5m | Oct-26/28 leaked `idle in transaction` conns | incident evidence (Oct-26/28 census) |
| PoolExhausted (CR-4) | `increase(ensemble_db_pool_requests_errors_total{pool="checkpoint"}[5m]) > 0` | 0m | PoolTimeouts (rebased on requests_errors superset) | event-style (first occurrence); CR-4 |
| LoopStarved (MA-4) | `ensemble_loop_lag_seconds{stat="p95"} > 2 and on() ensemble_loop_lag_samples > 150` | 5m | 116–134% CPU wedges | `>2` s lag: incident evidence (Oct-26/28); `>150` samples = half of the 300-sample rolling window (`lag_probe.py:59`) |
| CheckpointSlow (CR-3) | `histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket{op="aget"}[10m])) > 5 and on() increase(ensemble_checkpoint_op_seconds_count{op="aget"}[10m]) > 0` | 10m | 73–99 s reads | `>5` ← `DEFAULT_SLOW_SAVER_CUT_MS = 5000` (`daemon/checkpoint_perf.py:60`) |
| CheckpointBloat | `ensemble_checkpoint_blobs_table_bytes > 8e9` | 30m | 14 GB table / TOAST 10× bloat | `>8e9` bytes (8 GB) ← incident evidence (14 GB table, Oct-26/28 bench) |
| JournalWalkRisk | `ensemble_journal_file_bytes > 200000 or ensemble_journal_history_entries > 1000` | 5m | O(n²) bash journal walk (99.9% spins) | `>200000` bytes / `>1000` entries ← incident evidence (state.json O(n²) walk) |
| SweepHotLoop (MA-6) | `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2` | 5m | 8 s vs 300 s configured hot loop | `>2` cycles per 300 s window ← sweep interval registry 300 s (R15) + MA-6 |
| LostWake | `ensemble_waiting_children_max_age_seconds > 1800` | 5m | Parked lanes (lost wakes after restarts) | `>1800` s (30 min) ← incident evidence (parked lanes, Oct-26/28) |
| BashSpin (MA-7) | see fenced block + V-8 design | 5m | 99.9% bash spins | per-tick CPU fraction `>0.9` (one core) ← incident evidence (99.9% spins) |
| FalseDegraded (MA-5) | `rate(ensemble_readiness_probe_total{outcome=~"probe_timeout\|read_timeout"}[5m]) > 0.1 and on() ensemble_pg_backends{state="active"} < 3` | 5m | P2 deadline false-negative | `>0.1` (probe rate) ← incident evidence (P2 5 s deadline vs 17 s reads); classes `probe_timeout` (orchestrator attributes the timeout) and `read_timeout` (checkpoint-saver read with lock free) are the two that replay the incident |
| WedgeCaptureFired | `increase(ensemble_wedge_capture_total[5m]) > 0` | 0m | Sustained CPU / loop-lag / concentration wedge | event-style (first occurrence) |
| TextfileStale (V-8) | `time() - ensemble_textfile_last_write_timestamp_seconds > 120` | 2m | Out-of-process textfile writer crashed / stalled while a bash script spins | `>120` s = 12× the 10 s sampler cadence (V-8) |

`for:` clauses are mandatory on every rule; 0m rules are event-style (fire on first occurrence).

### Authoritative rule text (fenced YAML — R-m1, unescaped pipes)

```yaml
# ExternalLivenessDown (CR-1)
groups:
- name: ensemble.external_liveness
  rules:
  - alert: ExternalLivenessDown
    expr: up{job="ensemble"} == 0 or absent(up{job="ensemble"})
    for: 1m
```

```yaml
# ScrapeDurationHigh (CR-1)
  - alert: ScrapeDurationHigh
    expr: scrape_duration_seconds{job="ensemble"} > 5
    for: 2m
```

```yaml
# PoolLeak
  - alert: PoolLeak
    expr: ensemble_pg_idle_in_txn_oldest_seconds > 60
    for: 5m
```

```yaml
# PoolExhausted (CR-4)
  - alert: PoolExhausted
    expr: increase(ensemble_db_pool_requests_errors_total{pool="checkpoint"}[5m]) > 0
    for: 0m
```

```yaml
# LoopStarved (MA-4) — fixed multi-vector (V-1): and on()
  - alert: LoopStarved
    expr: ensemble_loop_lag_seconds{stat="p95"} > 2 and on() ensemble_loop_lag_samples > 150
    for: 5m
```

```yaml
# CheckpointSlow (CR-3) — fixed multi-vector (V-1): and on()
  - alert: CheckpointSlow
    expr: histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket{op="aget"}[10m])) > 5 and on() increase(ensemble_checkpoint_op_seconds_count{op="aget"}[10m]) > 0
    for: 10m
```

```yaml
# CheckpointBloat
  - alert: CheckpointBloat
    expr: ensemble_checkpoint_blobs_table_bytes > 8e9
    for: 30m
```

```yaml
# JournalWalkRisk
  - alert: JournalWalkRisk
    expr: ensemble_journal_file_bytes > 200000 or ensemble_journal_history_entries > 1000
    for: 5m
```

```yaml
# SweepHotLoop (MA-6)
  - alert: SweepHotLoop
    expr: rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2
    for: 5m
```

```yaml
# LostWake
  - alert: LostWake
    expr: ensemble_waiting_children_max_age_seconds > 1800
    for: 5m
```

```yaml
# BashSpin (MA-7) — V-8: per-tick delta gauge OR rate() on cumulative counter; pipes unescaped
  - alert: BashSpin
    expr: ensemble_process_cpu_seconds{name=~"promote_sh|stage_sh|launcher"} > 0.9
    for: 5m
```

```yaml
# FalseDegraded (MA-5) — V-1 fixed multi-vector (and on()); V-2 taxonomy (probe_timeout | read_timeout)
  - alert: FalseDegraded
    expr: rate(ensemble_readiness_probe_total{outcome=~"probe_timeout|read_timeout"}[5m]) > 0.1 and on() ensemble_pg_backends{state="active"} < 3
    for: 5m
```

```yaml
# WedgeCaptureFired
  - alert: WedgeCaptureFired
    expr: increase(ensemble_wedge_capture_total[5m]) > 0
    for: 0m
```

```yaml
# TextfileStale (V-8)
  - alert: TextfileStale
    expr: time() - ensemble_textfile_last_write_timestamp_seconds > 120
    for: 2m
```

### V-8 — BashSpin external sampler design (stand-alone)

A spinning bash script cannot write its own `*.prom`. The textfile is produced by an **external
sampler**:

- **Owner:** the node_exporter textfile-collector writer (a small out-of-process sampler shipped
  alongside the daemon; devops-owned). It runs on a **pinned 10 s interval** (the per-tick delta
  assumes 10 s — `limits.metrics_sampler_interval_s` default 10 s is the advisory cadence; the
  textfile writer uses its own fixed 10 s tick and the alert math assumes that).
- **Mechanism:** each tick the sampler runs `ps -o %cpu=,comm=` (Darwin) / `/proc/<pid>/stat`
  (Linux) for the named processes (`promote.sh`, `stage.sh`, `launcher`) and writes the
  textfile `$INSTALL_DIR/data/metrics/ensemble_textfile.prom` with
  `ensemble_process_cpu_seconds{name="..."} <per-tick CPU seconds>` (a **per-tick delta gauge**)
  plus `ensemble_textfile_last_write_timestamp_seconds <unix_ts>` (the freshness gauge used by
  `TextfileStale`).
- **Spin → stale interaction:** if the named script spins, it may block the sampler's `ps`
  (unlikely — `ps` is separate) or, more realistically, the writer process itself can be killed
  / wedged. When the textfile goes **stale** (no write for >120 s) while a spin is in progress,
  `TextfileStale` fires (2 m `for:`) and the operator is alerted that the BashSpin signal is
  no longer trustworthy. The two alerts are complementary: `BashSpin` fires on high CPU;
  `TextfileStale` fires on missing freshness.
- **Expression fix:** the per-tick delta gauge is compared directly (`> 0.9` = >90% of one core
  in the last tick). If a future sampler instead emits a **cumulative counter**
  `ensemble_process_cpu_seconds_total{name}`, the rule becomes
  `rate(ensemble_process_cpu_seconds_total{name=~"promote_sh|stage_sh|launcher"}[1m]) > 0.9`.
  The sampler's output type (gauge delta vs cumulative counter) is pinned in the sampler's own
  contract and the alert is written to match.
- **No `\|` escapes** anywhere in the PromQL (R-m1).

### V-2 — FalseDegraded taxonomy note

`outcome="timeout"` (the v1 value) **does not exist** in the 11-class taxonomy
(`daemon/services/readiness.py:64-79`: `probe_timeout`, `read_timeout`, `pool_busy`,
`queue_stale`, `boot_incomplete`, `identity_mismatch`, `hard_component_red`, `not_serving`,
`pool_leak`, `unknown`, `ok`). The two classes that replay the P2 deadline false-negative are
`probe_timeout` (the orchestrator attributes a timeout to the probe — the primary replay) and
`read_timeout` (the checkpoint-saver read that timed out with the saver lock free). Both are
grace-eligible (`:83-89`). The catalog readiness outcome vocabulary (§8.3 / F9) matches the
taxonomy exactly (cross-checked).

### V-7 — Baseline column (per-rule citation)

Every threshold above cites its source (code constant or incident/bench evidence with file +
date); no threshold is left uncited. Verified anchors: CheckpointSlow `>5` ←
`DEFAULT_SLOW_SAVER_CUT_MS = 5000` (`daemon/checkpoint_perf.py:60`); LoopStarved `>2` ←
incident evidence + `lag_probe.py:59` (300-sample window, 150 = half); PoolLeak `>60` ←
incident evidence (Oct-26/28); CheckpointBloat `>8e9` ← incident evidence (14 GB table);
JournalWalkRisk `>200000`/`>1000` ← incident evidence (state.json O(n²) walk); SweepHotLoop
`>2` ← sweep interval registry 300 s (R15) + MA-6; LostWake `>1800` ← incident evidence;
BashSpin `>0.9` ← incident evidence (99.9% spins); ScrapeDurationHigh `>5` ← incident
evidence; TextfileStale `>120` ← V-8 (10 s cadence × 12).

## 9. Reuse map (never rebuild what these cover)

- R1 `checkpoint_metrics.py:273-315` registry + renderer (generalize into central registry; CR-3).
- R2 `checkpoint_perf.py:190,198` + slow-op ledger (128-deque RAM) — CheckpointSlow source; one-measurement-one-name.
- R3 `lag_probe.py` (300-sample rolling window at :59; p95 at **:115**, p50 :116, max :114; :185 is `snapshot_histogram()`) — export computed quantiles as gauges; dark when wedge-capture off (MA-4).
- R4 `wedge_capture/sampler.py` (reuses `resource_monitor/proc_readers.py`; one /proc dialect) — post-process bucketing for `thread_cpu_seconds{thread_group}` (MA-2).
- R5 `resource_monitor` spine (sampler 30 s, ring 48 h, flusher 60 s, retention sweep; 4 tables) — live on Darwin only after the WS-A producer fix **`feature/resource-monitor-flusher-fix@53547b915`** merges to latest (before P0 executes); the earlier "post-WS-A (flusher.py:770 fix)" framing was the refuted 42804 theory — corrected in catalog §11 C35 (MA-3).
- R6 `/api/resources/*` + `maintenance` + `/api/snapshots/metrics` surfaces — /metrics is a view over same services.
- R7 `maintenance.py:531-536` VACUUM_FULL_TABLE_BYTES_SQL — sums **three** relations (`checkpoints` + `checkpoint_writes` + `checkpoint_blobs`; there is **no** single-table blob-size query) — blob table size source for `ensemble_checkpoint_blobs_table_bytes` (a split-out of that one round trip).
- R8 `readiness.py` 11-class taxonomy (**:64-79**; grace subset :83-89, components :94-99) + grace on /readyz — preserve labels, no re-probe (MA-5).
- R9 MANAGED_KEYS registry (settings_service.py:190) — bounds pattern for new metrics keys.
- R10 `worker_pool.py` `get_stats` (:1518, incl. wakeup_efficiency) — worker utilization source.
- R11 `db_capture.py` census discipline — `application_name` + fragment→site map + statement_timeout (MA-13).
- R12 collector.py's own "A2" vocabulary (pool_checked_out derived, requests_errors superset) — pool honesty (m2). *(This is the collector's vocabulary name, not an amendment reference.)*
- R13 `ens_db_tools.py:1157` pool status seam — resolves to catalog F4's single pool-status family `ensemble_db_pool_native{pool,quantity}` (no second `ensemble_db_pool_status` metric is shipped).
- R14 MCP health surfaces — **server state** from the per-boot quarantine registry (`mcp/init_guard.py:145-147`, published read-only at `/readyz` → `detail.mcp`); **gateway session state** from the MCPS MachineStore (`services/mcps_gateway/store.py:79`,`:103` → sessions / tombstones / audit).
- R15 sweep interval registry (300 s / 90 s) — SweepHotLoop baseline.
- R16 `MAINTENANCE_ENDPOINTS_ENABLED` gating pattern — kill-switch analog.
- R17 `message_tap.py` per-turn seam — tool-call perf source (m7, not graph internals).
- R18 textfile collector (genuinely new) — R18 is the only greenfield piece; everything else reuses.

## 10. Acceptance criteria

Testable (MA-9/MA-10). Each AC that depends on a dark-able surface carries an explicit
precondition row (V-9).

| # | Acceptance criterion | Precondition (dark-able surface) |
|---|---|---|
| AC1 | Incident replay: for each row in §1, the corresponding alert fires within 5 min using only these metrics, verified against Oct-26/28 timelines (fault-injection fixtures per alert — MA-9). | None (pure PromQL over fixture series) |
| AC2 | Self-cost: daemon CPU delta ≤1% measured via `/api/resources/self-cost` under a 15 s scrape (MA-9). | **WS-A merge precondition (V-9):** `/api/resources/self-cost` is DARK on macOS until `feature/resource-monitor-flusher-fix@53547b915` merges to latest (the spine producer was dead on Darwin — no `/proc`). Gate AC2 sign-off on the WS-A merge; the Linux-host alternative is to run AC2 on a Linux host where the producer is live; the macOS fallback is to measure self-cost via the listener thread's own CPU (listener-thread CPU delta) until WS-A merges. Do NOT sign off AC2 on macOS pre-merge. |
| AC3 | Cardinality census: daemon-origin ≤2 k series; stock exporters (node_exporter, postgres_exporter) measured separately and not counted against the daemon budget (MA-10). | None |
| AC4 | Scrape cost: `/metrics` returns in <50 ms on a healthy daemon; empty-shape 200 in <1 ms when dark (MA-15). | None (listener is pure stdlib socket, no `/proc` dependency) |
| AC5 | No pool pressure: census on dedicated non-pooled connection; no new pool slots consumed by scraping. | None |
| AC6 | No loop-lag regression: p95 loop lag unchanged under scrape load (≤ baseline +10%). | **Wedge-capture enablement precondition (V-9):** lag-probe-derived `ensemble_loop_lag_seconds` is dark when `ENSEMBLE_WEDGE_CAPTURE_ENABLED=false` (MA-4). Gate AC6 on `ENSEMBLE_WEDGE_CAPTURE_ENABLED=true` (or measure against the thread-CPU sampler, which is independent). |
| AC7 | Incident-replay within 5 min per incident row (§1 table). | Same as AC1; plus AC2/AC6 preconditions where the replay depends on a dark surface |
| AC8 | **All alert rules pass `promtool test rules` evaluation fixtures** (not just `promtool check rules` syntax). For each of the 14 rules: input series (concrete samples with labels/values), eval time, and EXPECTED result (fires / does not fire). Includes (a) one fixture reproducing the pre-fix V-1 defect (disjoint-label `and` → empty vector → no-fire) and (b) one fixture proving the fixed expression (with `and on()`) fires. Fixtures rendered as fenced ```yaml blocks (promtool unit-test format). `promtool check rules` alone is no longer sufficient. | None (fixtures are synthetic) |
| AC9 | Bucket extension verified: a 73–99 s checkpoint read produces a finite bucket (not +Inf) and CheckpointSlow fires only when p95 > 5 s sustained (CR-3). | None |
| AC10 | PoolExhausted verified against `requests_errors` superset with documented caveat (CR-4). | None |
| AC11 | Textfile freshness: `TextfileStale` does not fire when the sampler writes within 10 s; fires within 2 m when the writer stops. | WS-A / host-exporter deployment (node_exporter textfile collector) |
| AC12 | Vocabulary freeze (P-1): the P0 freeze does not freeze tool-categories from a hand list — it freezes from the V-4 generated artifact or defers the freeze with the `other`-fold as closure. | V-4 artifact generator landed (CI test) |

### promtool evaluation fixture format (AC8, one per rule)

Render each fixture as a fenced ```yaml block in promtool unit-test format. Example (LoopStarved, showing the V-1 defect + fix pair):

```yaml
# promtool test rules — LoopStarved
rule_files:
  - alerts.yml
evaluation_interval: 1m
tests:
  - interval: 1m
    input_series:
      - series: 'ensemble_loop_lag_seconds{stat="p95"}'
        values: '0 0 0 3 3 3'          # lag spikes to 3s at t=4
      - series: 'ensemble_loop_lag_samples'
        values: '200 200 200 200 200 200'   # disjoint label set (no stat label)
    alertname: LoopStarved
    # (a) pre-fix expression used bare `and` -> empty vector -> never fires
    # (b) fixed expression uses `and on()` -> fires
    expected:
      alertname: LoopStarved
      result: fires
```

(One such block per rule; the V-1 defect/repro pair is included inside the LoopStarved fixture
and mirrored for FalseDegraded and CheckpointSlow.)

## 11. Non-goals

Distributed tracing / APM; per-request HTTP histograms; log aggregation pipelines (journald stays as-is); synthetic canaries; high-cardinality per-instance breakdowns beyond fixed vocabularies; skill-metrics lane `/api/skills/{id}/metrics` (m5 — already has its own surface; re-surfacing would duplicate and double-count); VictoriaMetrics/Mimir compat claim (m9 — dropped unless tested; target is Prometheus+Grafana); k8s deploy (CR-5 — separate project). **Per-child (T3) RSS/CPU attribution on macOS (WS-A correction, 2026-10-29 — DECIDED, implement as decided):** per-child attribution stays `/proc`-only (`read_child_snapshot` → `resource_pid_map`) **even after the WS-A merge** — the Darwin ps-based reader (`feature/resource-monitor-flusher-fix@53547b915`) is a coarse daemon/process-level view, not a per-pid sampler, and this plan does NOT silently promise per-child on macOS. Consequences: per-child families/values are dark on Darwin (catalog §13.4); `coverage_pct` reflects the degraded rss-denominated proxy there; no partial per-child data ships on macOS. A future per-pid Darwin story is a new, separately-scoped decision.

## 12. Open questions

- OQ-1: Should `ensemble_loop_lag_seconds` remain a histogram or be exported as gauges (p50/p95) given the lag probe's 300-sample rolling window is not a Prometheus histogram? (MA-4 — recommend gauges + document dependency; decide in P0.)
- OQ-2: Does the textfile dir live under `$INSTALL_DIR/data/metrics/` or a dedicated mount? (P0 decision; affects node_exporter textfile collector config.)
- OQ-3: Version label policy — include `version` label on every series or only on `ensemble_upgrade_release_current`? (MA-12 — recommend only on the release gauge to avoid churn; decide in P0.)
- OQ-4 (**RESOLVED 2026-10-29**): **no new event-kind vocabulary is required** — per-event counting is already covered by `ensemble_turn_events_total{source}` (catalog F20 — the 5 `SOURCE_*` tap constants) + `ensemble_instance_lifecycle_total{event}` (catalog F13 — the 7 lifecycle events). The events-subsystem owner confirms those two vocabularies at the P1c review; no greenfield family is added. See decisions.md §H H-11.

**Status of open questions (R-m7).** OQ-1, OQ-2, and OQ-3 **remain open** and are decided in P0 (see above) — they are not resolved by this round. OQ-4 is **RESOLVED** (2026-10-29, see decisions.md §H H-11). All other review items are resolved by the ratified decisions and the phase exit criteria above.
