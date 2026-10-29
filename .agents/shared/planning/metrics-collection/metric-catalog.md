# Metric Catalog (v2) — the per-family metric specification

**Date** 2026-10-29 · **Status** DRAFT — grounding for `plan-overview.md` + `decisions.md` ·
**Scope** the **daemon-origin** metric surface; exporter-origin series are §6, outside the ≤2k
daemon budget; skill-metrics (`/api/skills/{id}/metrics`) is a **non-goal**, not catalogued ·
**Verified against** worktree `agents-ensemble-wt-metrics-v2` @ `feature/metrics-collection`
(tip `9bbef71c1`) — every `file:line` was read there; corrections to v1 and to the inventory
digest are logged in §11 · **Compat** Prometheus scrape model + Grafana, OpenMetrics text; m9 —
VictoriaMetrics/Mimir **dropped**, no compatibility asserted for an untested stack.

---

## 0. Conventions

**0.1 Naming.** `ensemble_<subsystem>_<measure>[_<unit>]`; `_total` ⇒ counter; `_seconds` /
`_bytes` / `_ratio` ⇒ unit suffix. Names are **stable** — `plan-overview.md`'s alert table and
`decisions.md` use them verbatim; a rename is a breaking change.

**0.2 Canonical registry.** `daemon/checkpoint_metrics.py` (355 lines, **top-level**, not
`daemon/services/`) becomes the general registry, growing `register_counter` / `register_gauge` /
`register_histogram` / `register_summary` on top of the existing `Counter` (`:65`),
`_LabeledHistogram` (`:111`), `_HistogramSeries` (`:138`), `Histogram` (`:151`),
`render_metrics()` (`:304`). No `prometheus_client`, no OTel, no new PyInstaller surface.

**0.3 One measurement, one name (CR-3).** The two metrics that exist carry non-canonical names —
`message_api_checkpoint_list_total` (`:274`), `message_api_saver_op_latency_seconds` (`:282`).
Both are re-registered under canonical `ensemble_*` names; the old names ride as one-release
deprecated aliases. No second exposition path.

**0.4 Buckets.** Every histogram's bucket set is stated once, in the **bucket-set annex
(§8.11)**. The checkpoint latency set today spans `0.001 → 10.0` s
(`daemon/checkpoint_metrics.py:59-63`) — **too short**: the 17 s writes and 73–99 s reads fall
into `+Inf` and collapse p95 to 10.0. F7 extends the top to `300.0` s.

**0.5 Labels (MA-12).** Every label value set is written out in full — no `...`, no "etc." — and
lives in the **vocabulary annex (§8)**, the single place each set is defined, with the
`file:line` it was verified against. Family entries reference the annex by number.

**0.6 Three coexisting idioms (m10).** Each family names the idiom it draws from: **A** =
in-process registry (`daemon/checkpoint_metrics.py`; resets on restart) · **B** = PG table spine
(`resource_monitor` tables; durable) · **C** = log markers (`ensemble.log` — a *source* for
greenfield counters, never for a gauge). `/metrics` is a **VIEW** over these, never a parallel
collector (MA-3).

**0.7 Listener (CR-2).** All exposition goes through a **separate localhost-bound listener** on
its own port, one route (`GET /metrics`), bound `127.0.0.1` only — **never** a route on the main
public app (`daemon/api.py`, 8079 in dev). A bind failure degrades it to *disabled* (logged,
daemon continues), never a boot abort.

**0.8 Kill-switch (MA-1).** `ENSEMBLE_METRICS_ENABLED` gates **every scrape** individually,
default **OFF**. OFF ⇒ the listener returns `200` with a valid empty body; the registry keeps
accumulating so switching ON loses no history. Registered as managed key `limits.metrics_enabled`
in `MANAGED_KEYS` (`daemon/services/settings_service.py:190`) — the registry default must mirror
the pydantic Field default exactly (one truth; the A4-knob lesson).

**0.9 Phases.** P0 foundation (listener, kill-switch, registry generalization, census
discipline, spine health gate, vocabulary freeze); P1a/P1b/P1c per-subsystem rollout; P2
exporters + dashboards + burn-in. **Re-partition of v1** (v1 put "P1" on everything) — the order
is forced by the dependency graph, see §2.1.

**0.10 Owner roles (house roles).** `developer` (code) · `devops` (infra/alerting) ·
`reviewer` (correctness of the metric's meaning) · `tester` (verification + burn-in).

**0.11 Degradation (MA-15).** **No family renders a zero for "I have no data."** A family whose
dependency is off emits **no series** (absent ≠ zero) unless its contract says the zero is real
(pool "no waiting clients", spine "table accepting rows"). **0.12 Cross-restart counters** — §10.

---

## 1. Summary table

`reuse` = an existing instrument generalized; `greenfield` = none exists, seam named.

| # | Family — canonical names | Type | Phase | Source | Series |
|---|---|---|---|---|---|
| F0.1 | `ensemble_metrics_up`, `ensemble_metrics_scrape_requests_total`, `ensemble_metrics_exposition_seconds` | gauge/counter/hist | P0 | greenfield (listener seam) | 17 |
| F0.2 | `ensemble_metrics_registry_measurements` | gauge | P0 | reuse `checkpoint_metrics.py:304` | 1 |
| F0.3 | `ensemble_resource_spine_tables_accepting_rows`, `ensemble_resource_spine_rows_written_total` | gauge/counter | P0 | reuse (resource_monitor spine) | 8 |
| F0.4 | `ensemble_metrics_vocabulary_version` | gauge | P0 | greenfield (freeze marker) | 1 |
| F1 | `ensemble_loop_lag_seconds`, `ensemble_loop_lag_samples`, `ensemble_loop_lag_overflow_total`, `ensemble_loop_lag_probe_state` | gauge×3/counter | P1a | reuse `wedge_capture/lag_probe.py` | 9 |
| F2 | `ensemble_thread_cpu_seconds`, `ensemble_thread_count` | gauge×2 | P1a | reuse `wedge_capture/sampler.py` | 22 |
| F3 | `ensemble_wedge_capture_total`, `ensemble_wedge_capture_write_total` | counter×2 | P1a | reuse `wedge_capture/triggers.py` | 5 |
| F4 | `ensemble_db_pool_native`, `ensemble_db_pool_requests_errors_total` | gauge/counter | P1a | reuse `readiness.py:525`,`:552`; `collector.py:29` | 8 |
| F5 | `ensemble_db_pool_checked_out`, `ensemble_db_pool_waiting_clients` | gauge×2 | P1a | reuse (derived + native, kept apart) | 3 |
| F6 | `ensemble_pg_backends`, `ensemble_pg_idle_in_txn_oldest_seconds`, `ensemble_pg_leaked_txn`, `ensemble_pg_census_last_success_timestamp_seconds` | gauge×4 | P1a | greenfield (census seam, §9) | 15 |
| F7 | `ensemble_checkpoint_op_seconds`, `ensemble_checkpoint_op_bytes`, `ensemble_checkpoint_slow_ops_total`, `ensemble_checkpoint_list_total` | hist×2/counter×2 | P1b | reuse `checkpoint_perf.py:190`,`:198`,`:91` | 22→33 |
| F8 | `ensemble_checkpoint_blobs_table_bytes`, `ensemble_checkpoint_table_bytes`, `ensemble_checkpoint_versions_per_thread`, `ensemble_checkpoint_prune_runs_total` | gauge×3/counter | P1b | reuse `maintenance.py:531` + greenfield (per-thread) | 9 |
| F9 | `ensemble_readiness_probe_total`, `ensemble_readiness_grace_active`, `ensemble_readiness_failure_streak`, `ensemble_readiness_component_state` | counter/gauge×3 | P1b | reuse `readiness.py:64-79` | 53 |
| F10 | `ensemble_job_queue_depth`, `ensemble_job_claim_latency_seconds`, `ensemble_dead_letter_items`, `ensemble_job_queue_items_created_total` | gauge/hist/gauge/counter | P1b | reuse (admission_state) + greenfield (claim latency) | 21 |
| F11 | `ensemble_workers_total`, `ensemble_workers_busy`, `ensemble_worker_pool_wakeup_efficiency`, `ensemble_worker_pool_events_total`, `ensemble_task_heartbeat_age_seconds`, `ensemble_task_heartbeat_cadence_seconds` | gauge×3/counter/gauge×2 | P1b | reuse `worker_pool.py:1518`; `task/models.py:284` | 16 |
| F12 | `ensemble_sweep_cycle_seconds`, `ensemble_sweep_cycles_total`, `ensemble_sweep_errors_total`, `ensemble_sweep_rows_total` | gauge/counter×3 | P1b | reuse (6 sweeps + 7 RDRS lanes) | 69 |
| F13 | `ensemble_instance_status_count`, `ensemble_instance_liveness_count`, `ensemble_waiting_children_count`, `ensemble_waiting_children_max_age_seconds`, `ensemble_wake_turns_total`, `ensemble_instance_lifecycle_total` | gauge×4/counter×2 | P1b | reuse `instance/models.py:20-32` + greenfield (wake outcome) | 33 |
| F14 | `ensemble_llm_tokens_total`, `ensemble_llm_token_usage_missing_total` | counter×2 | P1c | reuse `graph.py:3760-3812` | 3 |
| F15 | `ensemble_llm_request_total`, `ensemble_llm_request_seconds` | counter/hist | P1c | reuse `llm_error_classifier.py:764-819` | 23 |
| F16 | `ensemble_llm_stream_stalls_total`, `ensemble_llm_stream_watchdog_sweep_total` | counter×2 | P1c | reuse `llm_stream_watchdog.py:456` | 5 |
| F17 | `ensemble_mcp_server_state`, `ensemble_mcp_failures_total` | gauge/counter | P1c | reuse `mcp/init_guard.py:145-147` | 40 |
| F18 | `ensemble_mcps_gateway_sessions`, `ensemble_mcps_gateway_session_total`, `ensemble_mcps_gateway_tombstones`, `ensemble_mcps_gateway_audit_total` | gauge×2/counter×2 | P1c | reuse `services/mcps_gateway/` | 9 |
| F19 | `ensemble_compaction_fired_total`, `ensemble_compaction_occupancy_ratio`, `ensemble_compaction_precall_estimate_tokens` | counter/gauge×2 | P1c | reuse `compaction.py:1128`,`:2299` | 7 |
| F20 | `ensemble_turn_events_total`, `ensemble_turn_duration_seconds`, `ensemble_tool_calls_total` | counter/hist/counter | P1c | reuse `message_tap.py:234` | 57 |
| F21 | `ensemble_journal_file_bytes`, `ensemble_journal_history_entries`, `ensemble_journal_op_seconds`, `ensemble_journal_ops_total`, `ensemble_upgrade_phase_seconds`, `ensemble_upgrade_phase_total`, `ensemble_upgrade_release_current`, `ensemble_upgrade_journal_events_total` | gauge×3/hist/counter×3/gauge | P1c | reuse `tools/upgrade_journal.py` | 66 |
| F22 | `ensemble_process_cpu_seconds` + host/PG exporter series | gauge | P2 | greenfield (textfile + off-the-shelf) | 5 shell / ~67 exporter |

**Daemon-origin total: 522** (F0.1→F21) + **5 shell-origin** = **527**. Exporter-origin ≈67 (§6).
**Budget ≤2k — 26 % consumed.**

---

## 2. P0 — Foundation

**2.1 Why this partition.** v1 put "P1" on everything. The dependency graph forces an order:
listener + registry + spine gate (P0) → runtime/pool/census (P1a) → checkpoint/jobs/sweeps/
instances (P1b) → LLM/MCP/compaction/journal (P1c) → exporters (P2). P1a comes first because
the census (F6) and the pool families (F4/F5) are the incident root-causes and they validate
the registry under load before 200+ more series land on it.

### F0.1 — Exposition listener & per-request kill-switch

- **Name:** `ensemble_metrics_up` (gauge, no labels) · `ensemble_metrics_scrape_requests_total{outcome}`
  (counter) · `ensemble_metrics_exposition_seconds` (histogram, buckets §8.11).
- **Type:** gauge; counter; histogram (13 series incl. `_count`/`_sum`). **Labels:** `outcome` ∈
  `ok`, `error` (§8.3). No other labels.
- **Source:** **greenfield** — seam is the new `daemon/services/metrics_listener.py`. Why
  greenfield: `daemon/api.py` has **no** `/metrics` route and the repo contains **no**
  `prometheus_client` / OTel import — no exposition endpoint exists at all. The listener is a
  separate app on its own bound port (CR-2), never a route on `daemon/api.py`'s app.
- **Phase** P0 · **Owner** developer (implement), devops (port + scrape config) ·
  **Series estimate** 1 + 2 + 13 + 1 (the `ensemble_metrics_up{component}` info split) = **17**.
- **Degradation:** kill-switch OFF ⇒ `ensemble_metrics_up` = `0`; the scrape counter still
  increments `outcome="ok"` (the scrape happened, the body was empty); **every other family
  renders no series**. The exposition histogram records the empty render, so the operator sees
  "listener alive, switch off".

### F0.2 — Canonical registry generalization

- **Name:** `ensemble_metrics_registry_measurements` (gauge, no labels). **Type:** gauge — count
  of registered measurements. **Labels:** none.
- **Source:** **reuse** — `daemon/checkpoint_metrics.py:304` `render_metrics()` becomes the
  registry renderer; the two existing singletons (`:273`, `:281`) become registry-registered
  instances under their canonical names.
- **Phase** P0 · **Owner** developer; reviewer signs the naming migration · **Series estimate** **1**.
- **Degradation:** always present — a property of the registry, not of a dependency.

### F0.3 — Resource-spine health gate

- **Name:** `ensemble_resource_spine_tables_accepting_rows{table}` (gauge) ·
  `ensemble_resource_spine_rows_written_total{table}` (counter). **Type:** gauge; counter.
  **Labels:** `table` ∈ `resource_daemon_bucket`, `resource_instance_sample`,
  `resource_pid_map`, `resource_limit_policy` (§8.9).
- **Source:** **reuse** — the resource-monitor spine. `daemon/services/resource_monitor/flusher.py`
  writes in one `engine.begin()` window per flush (`flusher.py:336`); the counters are
  `FlusherCounters` (`flusher.py:161`). `resource_limit_policy` accepts zero rows **by design**
  (`daemon/routers/resources.py:1095`) ⇒ its gauge renders `1` (accepting) while its row counter
  stays `0` — the two answer different questions and the asymmetry is deliberate.
- **Phase** P0 · **Owner** developer · **Series estimate** 4 + 4 = **8**.
- **Degradation:** resource monitor disabled (`RESOURCE_MONITOR_ENABLED=false`,
  `daemon/config.py:854`) ⇒ **both metrics render no series** and the spine-derived families
  render nothing.
- **P0 exit criterion:** **all four tables accepting rows.** The per-instance tier is broken by
  the `estimated` column's `INTEGER`/`BOOLEAN` mismatch (PG SQLSTATE 42804, `DatatypeMismatch`),
  raised every flush window and rolling back all four tables through the shared
  `engine.begin()`. The flusher still binds a Python `bool` at
  `daemon/services/resource_monitor/flusher.py:770`; the runtime correction is
  `daemon/manager.py:7341` `_ensure_postgres_columns` (the migration
  `daemon/migrations/versions/20261019_120822_alter_resource_monitor_booleans.sql` is the audit
  document — the PG migration runner is a NO-OP). **F0.3 does not report green until the spine
  writes land on real PostgreSQL and the 4-table census is non-zero.**

### F0.4 — Vocabulary freeze marker

- **Name:** `ensemble_metrics_vocabulary_version` (gauge, no labels). **Type:** gauge — an
  integer pinned to the §8 annex revision. **Labels:** none.
- **Source:** **greenfield** — a module-level int in `daemon/checkpoint_metrics.py`. Why
  greenfield: no existing version marker exists for a label vocabulary.
- **Phase** P0 · **Owner** developer; reviewer owns the annex text · **Series estimate** **1**.
- **Degradation:** always present.

---

## 3. P1a — Runtime, wedge, pools, PostgreSQL census

### F1 — Event-loop lag (MA-4)

- **Name:** `ensemble_loop_lag_seconds{stat}` (gauge) · `ensemble_loop_lag_samples` (gauge) ·
  `ensemble_loop_lag_overflow_total` (counter) · `ensemble_loop_lag_probe_state{state}` (gauge).
  **Type:** gauges + one counter — **NOT a histogram**: MA-4, the probe is a 300-sample rolling
  window with a *computed* p95, not buckets. Computed quantiles ship as gauges.
  **Labels:** `stat` ∈ `p50`, `p95`, `max` · `state` ∈ `initializing`, `ok`, `unknown`, `stopped`
  (§8.3).
- **Source:** **reuse** — `daemon/services/wedge_capture/lag_probe.py`. `MAX_LAG_SAMPLES = 300`
  (`:59`, ≈5 min at 1 Hz); quantiles computed in `LagHistogram.as_dict()` — `max` `:114`, `p95`
  `:115`, `p50` `:116` — nearest-rank (`:107`); `overflow_count` `:115-119`; snapshot entry
  `LoopLagProbe.snapshot_histogram()` `:185`.
- **Phase** P1a · **Owner** developer (wire), devops (alert) · **Series estimate** 3+1+1+4 = **9**.
- **Degradation:** the probe is **dark when `ENSEMBLE_WEDGE_CAPTURE_ENABLED` is off** (the
  watchdog thread that drives it never starts). Dark ⇒ `ensemble_loop_lag_samples` = `0`, the
  `stat` gauges render **no series**, `ensemble_loop_lag_overflow_total` = `0`,
  `ensemble_loop_lag_probe_state{state="stopped"}` = `1`. The dependency is on the HELP text.
- **Alert change (MA-4):** v1's `histogram_quantile(0.95, rate(ensemble_loop_lag_seconds_bucket[5m])) > 2`
  is **invalid** (no `_bucket` series exists). Correct: `ensemble_loop_lag_seconds{stat="p95"} > 2`.

### F2 — Thread CPU attribution (MA-2)

- **Name:** `ensemble_thread_cpu_seconds{thread_group}` (gauge) · `ensemble_thread_count{thread_group}`
  (gauge). **Type:** gauge — CPU seconds accumulated by the group since the last sample (a
  per-tick jiffy delta, **not** a monotonic counter). **Labels:** `thread_group` ∈ 11 values (§8.2).
- **Source:** **reuse** — `daemon/services/wedge_capture/sampler.py` is the ONE `/proc` walker
  (per-thread jiffies `:160`, process jiffies `:175`, `/proc/self/task/<tid>/comm` `:151`,
  Python-thread-name map `:192`). **One-/proc-dialect rule:** post-process bucketing only, never
  a second sampler (MA-2). **P1a prerequisite:** worker threads are **unnamed** — `Worker` calls
  `super().__init__(daemon=True)` at `daemon/services/worker_pool.py:253` with no `name=`, so
  every worker is `Thread-N` and the `worker_pool` vs `chat_worker_pool` split is **not
  observable from `/proc` today**. P1a must name them at construction
  (`name=f"worker-{lane}-{worker_id}"`; lane vocabulary `default` / `chat`,
  `daemon/services/worker_pool.py:207`) before F2 can emit those two groups honestly. Until then
  both fold into `other`.
- **Phase** P1a · **Owner** developer · **Series estimate** 11 + 11 = **22**.
- **Degradation:** wedge capture off ⇒ the sampler does not run ⇒ **F2 renders no series**.

### F3 — Wedge-capture counters

- **Name:** `ensemble_wedge_capture_total{reason}` (counter) ·
  `ensemble_wedge_capture_write_total{outcome}` (counter). **Type:** counter; counter.
  **Labels:** `reason` ∈ `sustained_cpu`, `sustained_loop_lag`, `thread_concentration` ·
  `outcome` ∈ `ok`, `failed` (§8.3).
- **Source:** **reuse** — trigger evaluation sites
  (`daemon/services/wedge_capture/triggers.py:202`, `:237`, `:281`) and the watchdog's capture
  dispatch (`daemon/services/wedge_capture/watchdog.py:417`).
- **Phase** P1a · **Owner** developer · **Series estimate** 3 + 2 = **5**.
- **Degradation:** wedge capture off ⇒ **no series** (the trigger never fires). The v1
  `ensemble_db_pool_checkout_timeouts` family is deliberately **absent** here — see F4.

### F4 — DB pool native gauges + request errors (CR-4)

- **Name:** `ensemble_db_pool_native{pool,quantity}` (gauge) ·
  `ensemble_db_pool_requests_errors_total{pool,kind}` (counter). **Type:** gauge; counter.
  **Labels:** `pool` ∈ `manager`, `checkpoint` · `quantity` ∈ `size`, `max`, `available`,
  `overflow` (native quantities only) · `kind` ∈ `waiting_exception`, `too_many_requests`,
  `unclassified` (§8.9).
- **Source:** **reuse** — `daemon/services/readiness.py:525` `sample_sqlalchemy_pool_state`
  (`checkedout`/`size`/`overflow`) and `:552` `sample_psycopg_pool_state` (`pool_size`/
  `pool_max`/`pool_available`/`requests_waiting`); the same shape is published at `/readyz` →
  `detail.pool_state` (`daemon/api.py:5015`).
- **CR-4 — the timeout counter does not exist.** `psycopg_pool` 3.3.1 exposes no
  checkout-timeout counter. `daemon/services/wedge_capture/collector.py:24-26` states it
  verbatim: `pool_checkout_timeouts` is **ALWAYS `None`** with
  `pool_checkout_timeouts_reason = "not_exposed_by_psycopg_pool_3.3.1"`, and `requests_errors`
  "is a SUPERSET — it counts every waiting exception plus `TooManyRequests`". Therefore:
  (a) `ensemble_db_pool_requests_errors_total` is the primary family, **documented as a
  superset, not a timeout count**; (b) `kind="unclassified"` is the **only** value emitted today
  (psycopg exposes the aggregate, not the breakdown) — the `kind` label exists so the follow-up
  seam can split it without a breaking label change; (c) the v1 name
  `ensemble_db_pool_checkout_timeouts_total` is **retired** and the PoolExhausted alert is
  **rebased** onto `increase(ensemble_db_pool_requests_errors_total[5m]) > 0`; (d) **optional
  follow-up (outside this catalog's P1 scope):** instrument the daemon's own checkout seams to
  emit `kind="waiting_exception"` / `kind="too_many_requests"` precisely. The SQLAlchemy manager
  pool raises `TimeoutError` on checkout and has **no** `requests_errors` equivalent ⇒
  `pool="manager"` renders **no** series for this counter — an absence, not a gap.
- **Phase** P1a · **Owner** developer · **Series estimate** native gauge: `manager` yields
  `size`,`max`,`available`,`overflow` (4), `checkpoint` yields `size`,`max`,`available` (3;
  psycopg has no `overflow`) ⇒ **7**; request-errors counter: `checkpoint` only,
  `kind="unclassified"` ⇒ **1**. Total **8**.
- **Degradation:** the saver pool is not pool-backed (SQLite) or `get_stats` unavailable ⇒
  `sample_psycopg_pool_state` returns `None` ⇒ the `pool="checkpoint"` series render no series.
  Absent manager pool ⇒ `pool="manager"` renders no series.

### F5 — DB pool derived vs native quantities (m2)

- **Name:** `ensemble_db_pool_checked_out{pool}` (gauge) ·
  `ensemble_db_pool_waiting_clients{pool}` (gauge). **Type:** gauge. **Labels:** `pool` ∈
  `manager`, `checkpoint` (§8.9).
- **Source:** **reuse**, with the derived/native split kept honest (m2):
  `ensemble_db_pool_checked_out` is **DERIVED** — `pool_size − pool_available`, clamped at 0
  (there is no native key; `daemon/services/wedge_capture/collector.py:19-20`) — and it is a
  **separate metric** so the derivation is visible in the name, not merged into a `state=` label
  that would pretend parity with native quantities. `ensemble_db_pool_waiting_clients` is
  **NATIVE** — `requests_waiting`, which exists on the **async pool only** (`collector.py:23`);
  the manager (sync SQLAlchemy) pool has no such counter ⇒ `pool="manager"` renders **no series**.
- **Phase** P1a · **Owner** developer · **Series estimate** 2 + 1 = **3**.
- **Degradation:** same as F4 — absent pool ⇒ absent series.

### F6 — PostgreSQL census (MA-13)

- **Name:** `ensemble_pg_backends{state}` (gauge) ·
  `ensemble_pg_idle_in_txn_oldest_seconds` (gauge) · `ensemble_pg_leaked_txn{site}` (gauge) ·
  `ensemble_pg_census_last_success_timestamp_seconds` (gauge). **Type:** gauges.
  **Labels:** `state` ∈ 7 values (PostgreSQL's `pg_stat_activity.state` domain, snake-cased for
  label safety, plus `unknown` for NULL) · `site` ∈ the 6-value fixed fragment→site map
  (§8.9, §9.2).
- **Source:** **greenfield** — seam is a **new** module `daemon/services/metrics_census.py`. Why
  greenfield: the daemon has **no** `pg_stat_activity` census today. The closest artefacts are
  the wedge-capture's DB sub-capture (`daemon/services/wedge_capture/db_capture.py`, which runs
  **only during a capture**, on a one-shot connection with
  `CAPTURE_APPLICATION_NAME = "wedge-capture"` at `:62`) and the readiness leak oracle
  (`daemon/services/readiness.py:784` `_evaluate_leak`, which reads *pool counters*, not
  `pg_stat_activity`). Neither is a census. The census inherits `db_capture.py`'s discipline
  (§9) — dedicated non-pooled connection, `application_name='ensemble-metrics-census'`,
  `statement_timeout`, ≥15 s cadence.
- **Phase** P1a · **Owner** developer (census), devops (connection grants) · **Series estimate**
  7 + 1 + 6 + 1 = **15**.
- **Degradation:** census disabled / connection refused / `statement_timeout` ⇒ **all four
  metrics render no series** and `ensemble_pg_census_last_success_timestamp_seconds` goes stale —
  a stale timestamp is the operator's cue (the metric is the census's own health signal). The
  census **never** runs through the P1/P2 pools (§9.1).

---

## 4. P1b — Checkpoint, jobs, sweeps, instances, lifecycle

### F7 — Checkpoint op latency / bytes / slow ops (CR-3)

- **Name:** `ensemble_checkpoint_op_seconds{op}` (histogram) ·
  `ensemble_checkpoint_op_bytes{op}` (histogram) · `ensemble_checkpoint_slow_ops_total{op}`
  (counter) · `ensemble_checkpoint_list_total` (counter). **Type:** histogram; histogram;
  counter; counter. **Labels:** `op` ∈ `aget`, `aput`, `adelete`, `alist` (§8.9 — the set
  declared in the registry HELP text, `daemon/checkpoint_metrics.py:281-290`; `alist` is
  migration-only). Buckets §8.11.
- **Source:** **reuse — do not duplicate** (CR-3). The observation sites already exist:
  `daemon/checkpoint_perf.py:190` (log-gated path) and `:198` (ungated path) both call
  `saver_op_latency_seconds.labels(op=op).observe(...)`; `:191` / `:199` call
  `record_slow_saver_op(...)` — the bounded slow-op ledger (`:91`,
  `SLOW_SAVER_LEDGER_MAXLEN = 128` at `:63`, `DEFAULT_SLOW_SAVER_CUT_MS = 5000` at `:60`).
  **The slow-op ledger is already the CheckpointSlow alert source — REUSE it; do not build a
  parallel detector.** `ensemble_checkpoint_list_total` replaces
  `message_api_checkpoint_list_total` (`daemon/checkpoint_metrics.py:273`), incremented via
  `daemon/checkpoint_perf.py:218`. **Bytes:** `daemon/checkpoint_perf.py:148`
  `log_saver_op(..., bytes_=0, deleted=0)` already carries `bytes_` at the seam ⇒ the bytes
  histogram is a **reuse observation, not a new instrument** — but the hot path `time_saver_op`
  (`daemon/checkpoint_perf.py:202`, docstring `:205`) forwards `bytes_=0`, so the bytes histogram is
  **under-reported for `aget`** until P1b wires the real blob byte count.
- **Phase** P1b · **Owner** developer; reviewer pins the bucket set · **Series estimate** only
  `op="aget"` fires in production (the sole prod call site is `daemon/persistence.py:509`,
  `time_saver_op("aget", ...)`) ⇒ seconds histogram 19, bytes histogram 0 today → 10 after the
  byte wiring, slow-ops counter 1, list counter 1 ⇒ **22 today, 33 after the byte wiring**.
  Fully-wired ceiling: 76 + 40 + 4 + 1 = 121.
- **Degradation:** the metric side-effect is recorded **regardless of the log gate**
  (`daemon/checkpoint_perf.py:187-189` — "Metric records REGARDLESS of the log gate"), so
  `CHECKPOINT_PERF_LOGS=0` does **not** starve the histogram. A non-psycopg backend (SQLite)
  ⇒ the `op` series are absent.

### F8 — Checkpoint store size gauges

- **Name:** `ensemble_checkpoint_blobs_table_bytes` (gauge) ·
  `ensemble_checkpoint_table_bytes{table}` (gauge) ·
  `ensemble_checkpoint_versions_per_thread` (gauge, p95) ·
  `ensemble_checkpoint_prune_runs_total{outcome}` (counter). **Type:** gauge; gauge; gauge;
  counter. **Labels:** `table` ∈ `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` ·
  `outcome` ∈ `dry_run`, `executed`, `skipped_non_postgres`, `error` (§8.9).
- **Source:** **reuse** — `daemon/services/maintenance.py:531-536`
  `_VACUUM_FULL_TABLE_BYTES_SQL` sums `pg_relation_size` over exactly `checkpoints`,
  `checkpoint_writes`, `checkpoint_blobs`. `ensemble_checkpoint_blobs_table_bytes` is a
  **split-out** of that one round trip, and `ensemble_checkpoint_table_bytes{table}` is its
  generalization. The per-thread version count is **greenfield** — the seam is the
  `checkpoint_prune` candidate scan (`daemon/services/checkpoint_prune.py:224`), which already
  materialises `(thread_id, checkpoint_ns, _ckpt_count)` triples.
- **Phase** P1b · **Owner** developer · **Series estimate** 1 + 3 + 1 + 4 = **9**.
- **Degradation:** PostgreSQL only ⇒ on SQLite these render **no series**
  (`checkpoint_prune.py:195-199` is the refusal precedent). The query needs `pg_class` +
  `pg_relation_size` — read-only superuser is enough.

### F9 — Readiness probe outcomes and grace (MA-5)

- **Name:** `ensemble_readiness_probe_total{component,outcome}` (counter) ·
  `ensemble_readiness_grace_active` (gauge) · `ensemble_readiness_failure_streak{component}`
  (gauge) · `ensemble_readiness_component_state{component}` (gauge). **Type:** counter; gauge;
  gauge; gauge. **Labels:** `component` ∈ 4 (the composite's components) · `outcome` ∈ the
  **normative 11-class readiness taxonomy**; the grace-eligible subset is a **fixed subset
  published on the HELP text**, not a label (§8.3).
- **Source:** **reuse** — the composite is already computed by the background refresher and
  published at `daemon/api.py:5015` `/readyz` (`detail.component_classes`, `detail.grace`,
  `detail.leak_signal`, `detail.failure_streak`). MA-5: **grace is already on `/readyz` — the
  metric family READS the published composite, it never re-probes.**
- **Phase** P1b · **Owner** developer; devops owns the FalseDegraded alert · **Series estimate**
  4 × 11 = 44, + 1 + 4 + 4 = **53**.
- **Degradation:** no composite yet (refresher has not ticked) ⇒ the readiness payload carries
  the `boot_incomplete` class (`daemon/api.py:5053-5056`) ⇒
  `ensemble_readiness_probe_total{component=*,outcome="boot_incomplete"}` increments and
  `ensemble_readiness_grace_active` = `0`. A composite that cannot be reached at all ⇒ the
  family renders **no series**.

### F10 — Job queue depth and claim latency (m3)

- **Name:** `ensemble_job_queue_depth{admission_state}` (gauge) ·
  `ensemble_job_claim_latency_seconds` (histogram, buckets §8.11) ·
  `ensemble_dead_letter_items` (gauge) · `ensemble_job_queue_items_created_total` (counter).
  **Type:** gauge; histogram; gauge; counter. **Labels:** `admission_state` ∈ `queued`, `active`,
  `done`, `dead` (§8.10 — the `AdmissionState` enum, the **sole write authority** for queue
  gating).
- **Source:** **reuse** for depth (the `job_queue_items.admission_state` column with index
  `idx_job_queue_admission_state`, `daemon/repositories/job_queue/models.py:283`); the live
  surfaces already exist — `GET /api/queues`, `/api/jobs`, `/api/dlq`, `/api/work`.
  **Claim latency is GREENFIELD (m3):** there is **no** existing claim-latency instrument — the
  only related signal is the in-RAM `WorkerPool._stats["empty_claim_attempts"]`
  (`daemon/services/worker_pool.py:1383`), which counts empty polls, not enqueue→claim latency.
  **Seam:** the claim boundary inside `daemon/services/worker_pool.py` `wait_for_work` → the
  task-claim call, where the enqueue timestamp
  (`daemon/repositories/message_queue/models.py:83` `enqueued_at`) and the claim moment are both
  available. The observation is taken **once**, at the claim site, and carried — never
  re-derived downstream.
- **Phase** P1b · **Owner** developer · **Series estimate** 4 + 14 + 1 + 1 = **20** (the
  summary table rounds to 21; the extra is a per-queue `queue_id` variant deliberately **not**
  claimed — `queue_id` is unbounded).
- **Degradation:** queue repository unavailable ⇒ no depth series. `job_queue_items` carries
  **only** `created_at` as a TEXT ISO string (`daemon/repositories/job_queue/models.py:220`
  and `:357`) — no `started_at`/`completed_at` ⇒ **queue-item durations are NOT derivable**; any
  such metric is greenfield by construction and is not claimed here.

### F11 — Worker pool utilization and task heartbeat age (m4)

- **Name:** `ensemble_workers_total{pool}` (gauge) · `ensemble_workers_busy{pool}` (gauge) ·
  `ensemble_worker_pool_wakeup_efficiency{pool}` (gauge) ·
  `ensemble_worker_pool_events_total{pool,event}` (counter) ·
  `ensemble_task_heartbeat_age_seconds` (gauge) · `ensemble_task_heartbeat_cadence_seconds`
  (gauge). **Type:** gauge; gauge; gauge; counter; gauge; gauge. **Labels:** `pool` ∈ `worker`,
  `chat` · `event` ∈ the **complete** `_stats` key set (§8.10).
- **Source:** **reuse** — `WorkerPool.get_stats()` (`daemon/services/worker_pool.py:1518`)
  returns `num_workers`, `notifications_sent`, `empty_claim_attempts`,
  `workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance` and the computed
  `wakeup_efficiency` (`:1537` — `notifications / max(1, notifications + empty_claims)`). The
  "busy" count is derived the way the live tool derives it
  (`daemon/tools/instance_read.py:2092` — a worker is busy when its `_current_task_id` is not
  `None`). These counters are in-RAM and **lost on restart** (§10).
  **Heartbeat age (m4):** source column `daemon/repositories/task/models.py:284`
  `last_heartbeat_at` — a `timestamp without time zone` column, so bind/compare with
  `now_utc_naive()` per the project timestamp convention. **Cadence:** the per-worker
  `TaskHeartbeat` thread updates it at `DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0`
  (`daemon/services/worker_pool.py:45`; constructor parameter at `:197`). The metric publishes
  the **configured cadence** as its own series, so the operator can tell "heartbeat 40 s old,
  cadence 30 s" (stale) from "heartbeat 40 s old, cadence 300 s" (fine). The value is read from
  the pool's constructor argument, never hard-coded.
- **Phase** P1b · **Owner** developer · **Series estimate** 2 + 2 + 2 + 8 + 1 + 1 = **16**.
- **Degradation:** no worker pool ⇒ the pool-scoped series render **no series**;
  `ensemble_task_heartbeat_age_seconds` renders `0` only when there are **zero** tasks (a real
  zero); with tasks present and no heartbeat row it renders the age of the oldest missing
  heartbeat — the alarm condition.

### F12 — Sweep cycle and row counters

- **Name:** `ensemble_sweep_cycle_seconds{lane}` (gauge) · `ensemble_sweep_cycles_total{lane}`
  (counter) · `ensemble_sweep_errors_total{lane}` (counter) ·
  `ensemble_sweep_rows_total{lane,outcome}` (counter). **Type:** gauge; counter; counter; counter.
  **Labels:** `lane` ∈ **13 values** (§8.4: the 7 RDRS lanes + the 6 standalone sweep services,
  with each configured interval); `outcome` ∈ 6 values (§8.4).
- **Source:** **reuse** — each service exposes `counters()`:
  `watch_reconcile_sweep.py:356` (`ticks`, `fired_total`, `retired_total`, `errors_total`),
  `eligible_pending_sweep.py:404` (`ticks`, `eligible_total`, `notified_total`, `errors_total`,
  `skipped_promote_hold_total`), `orphan_watcher_sweep.py:260` (`ticks`, `cancelled_total`,
  `errors_total`); the RDRS per-lane row counters supply the lane outcomes.
  **Doc drift to correct:** `daemon/services/job_queue_service.py:828-836` documents
  `orphan_released` as "a cumulative counter on the WatchReconcileSweepService", but the code
  returns it **per-tick** (`job_queue_service.py:837` `orphan_released = 0`, reset each call).
  The metric accumulates per-tick values into a cumulative counter — it never exposes the
  per-tick value directly. **The 8 s hot loop:** v1's `rate(ensemble_sweep_cycles_total[5m]) * 300 > 2`
  becomes `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2`;
  the configured interval per lane is published on the HELP text, so an operator compares actual
  vs configured without a second metric.
- **Phase** P1b · **Owner** developer · **Series estimate** 13 + 13 + 13 + ≈30 live
  (lane, outcome) combinations (most lanes emit 2–3 of the 6 outcomes) = **≈69**.
- **Degradation:** a lane disabled (the RDRS lanes are individually kill-switchable,
  `daemon/config.py:1951-2020`) ⇒ that lane renders **no** cycle/cycle-count series; its error
  counter renders `0` — a real zero ("the lane ran zero times because it is off") differs from
  "the lane errored".

### F13 — Instance status, lost wakes, lifecycle (m1, m8)

- **Name:** `ensemble_instance_status_count{status}` (gauge) ·
  `ensemble_instance_liveness_count{liveness}` (gauge) · `ensemble_waiting_children_count`
  (gauge) · `ensemble_waiting_children_max_age_seconds` (gauge) ·
  `ensemble_wake_turns_total{outcome}` (counter) · `ensemble_instance_lifecycle_total{event}`
  (counter). **Type:** gauge; gauge; gauge; gauge; counter; counter. **Labels:** `status` ∈ the
  **complete 10-value** `InstanceStatus` enum · `liveness` ∈ 7 · `outcome` ∈ 7 (wake-turn) ·
  `event` ∈ 7 (lifecycle) (§8.1, §8.10). m8: lifecycle **beyond** point-in-time, including
  **revive-on-`send_message`** (the lost-wakes class).
- **Source:** **reuse** for status/liveness (the live census is already computed by
  `daemon/tools/instance_read.py:2019` `_build_capacity_summary`; `counts_by_status` and
  `counts_by_liveness` are the same two buckets) and for the wake event counters
  (`daemon/services/worker_pool.py:1381-1386` already tracks `empty_claim_attempts` and
  `claims_skipped_due_to_busy_instance`). **GREENFIELD for `ensemble_wake_turns_total` (m1)** —
  the Carrier-Threading contract (producer / carrier / threading / consumer) is specified in
  full in **§8.13**.
- **Phase** P1b · **Owner** developer (producer + carrier), reviewer (Carrier-Threading
  compliance), devops (LostWake alert) · **Series estimate** 10 + 7 + 1 + 1 + 7 + 7 = **33**.
- **Degradation:** instance census unavailable ⇒ the two count gauges render **no series**; the
  wake/lifecycle counters are in-RAM and reset on restart (§10).

---

## 5. P1c — LLM, MCP, compaction, tool-call, journal twin

### F14 — LLM token accounting (MA-8)

- **Name:** `ensemble_llm_tokens_total{direction}` (counter) ·
  `ensemble_llm_token_usage_missing_total` (counter). **Type:** counter; counter.
  **Labels:** `direction` ∈ `input`, `output` (§8.10).
- **Source:** **reuse** — the streaming chunk converter attaches `chunk.message.usage_metadata`
  at `daemon/graph.py:3811-3812`, built from the terminal usage chunk at `:3771-3774`.
  **Documented caveat (MA-8):** `usage_metadata` is a **stale/undercounting proxy**
  (`daemon/graph.py:7632` — "never the primary") and is **lost entirely** when the backend
  omits the final usage chunk (`daemon/graph.py:3873-3881` — the `stream_usage` injection is
  what requests it; without it `usage_metadata` is `None`).
  `ensemble_llm_token_usage_missing_total` counts observations where `usage_metadata` was
  absent — the **undercount caveat made visible** rather than hidden.
- **Phase** P1c · **Owner** developer · **Series estimate** 2 + 1 = **3**.
- **Degradation:** non-streaming call sites (`streaming=False` preserved verbatim,
  `daemon/graph.py:3889-3891`) ⇒ `usage_metadata` may be absent ⇒ the tokens counter does not
  increment and the missing counter does. **No series are removed.**

### F15 — LLM request outcome and latency (MA-8, the real 4-layer ladder)

- **Name:** `ensemble_llm_request_total{outcome}` (counter) · `ensemble_llm_request_seconds`
  (histogram, buckets §8.11). **Type:** counter; histogram — the latency histogram carries **no**
  outcome label (14 series); the outcome breakdown lives on the counter. Splitting the histogram
  by outcome would cost 8 × 14 = 112 series for no analytical gain. **Labels:** `outcome` ∈ the
  **8-value enum reflecting the real 4-layer retry ladder** (`ok`, `transient_retry`,
  `timeout_retry`, `failover_to_backup`, `reset_to_primary`, `terminal_transient`,
  `terminal_permanent`, `context_overflow`) — the layer each value maps to is specified in
  **§8.12**.
- **Source:** **reuse** — the failover bookkeeping currently lives in the **in-closure dict**
  (`daemon/llm_error_classifier.py:818`) and is visible only as `[LLM-HA]` log lines. The
  registry observes at the same points; the closure dict is **not** a second source of truth.
  The predicate is built by `make_llm_retry_strategy` (`:764`).
- **Phase** P1c · **Owner** developer · **Series estimate** 8 + 14 = **22** (the summary table
  rounds to 23 with `3600.0` reserved for slow models).
- **Degradation:** always present (the LLM lane is always configured); an outcome with zero
  observations renders no series — the enum is the **contract**, not a promise that every value
  occurs.

### F16 — LLM stream stall

- **Name:** `ensemble_llm_stream_stalls_total{phase}` (counter) ·
  `ensemble_llm_stream_watchdog_sweep_total{outcome}` (counter). **Type:** counter; counter.
  **Labels:** `phase` ∈ `abort_attempt`, `aborted`, `recovered` (the three stages of the
  force-abort) · `outcome` ∈ `none`, `claimed` (the return shape of `sweep_once`) (§8.10).
- **Source:** **reuse** — `daemon/services/llm_stream_watchdog.py:456` `_force_unblock` is the
  abort site; the `[LLM-WATCHDOG-STALL]` marker is emitted at `:497-498`; the sweep is
  `sweep_once` (`:551`). The watchdog thread starts at `daemon/api.py:2212-2226` with
  `config.llm.stream_stall_threshold_seconds` (default 45 s,
  `daemon/services/llm_stream_watchdog.py:4-5`).
- **Phase** P1c · **Owner** developer · **Series estimate** 3 + 2 = **5**.
- **Degradation:** watchdog thread start failure ⇒ `daemon/api.py:2229` logs
  `[StreamWatchdog] DISABLED` and the daemon continues ⇒ **the family renders no series**
  (operator sees the absence plus the log line).

### F17 — MCP server health and failures

- **Name:** `ensemble_mcp_server_state{server,state}` (gauge) ·
  `ensemble_mcp_failures_total{server,kind}` (counter). **Type:** gauge; counter.
  **Labels:** `state` ∈ `ok`, `degraded`, `quarantined`, `unknown` · `server` ∈ the **bounded
  5-value roster** (3 built-ins + `custom` + `other`) with the churn policy · `kind` ∈ `connect`,
  `initialize`, `tool_call`, `quarantine` (§8.5).
- **Source:** **reuse** — the per-boot quarantine registry (`daemon/mcp/init_guard.py`) is
  already published read-only at `/readyz` → `detail.mcp` (`daemon/api.py:3840-3878`) and
  consumed by `_mcp_server_health_state` (`daemon/routers/mcp_servers.py:302`). The metric reads
  that snapshot; it **never re-probes** an MCP server.
- **Phase** P1c · **Owner** developer; devops (quarantine alert) · **Series estimate**
  5 × 4 + 5 × 4 = **40**.
- **Degradation:** the quarantine registry is unreachable ⇒ `/readyz` **omits** the `mcp` key
  (`daemon/api.py:3867-3873`, the older-consumer tolerance contract) ⇒ the family renders
  **no series**.

### F18 — MCPS gateway sessions and audit

- **Name:** `ensemble_mcps_gateway_sessions` (gauge) ·
  `ensemble_mcps_gateway_session_total{outcome}` (counter) ·
  `ensemble_mcps_gateway_tombstones` (gauge) · `ensemble_mcps_gateway_audit_total{outcome}`
  (counter). **Type:** gauge; counter; gauge; counter. **Labels:** session `outcome` ∈ `begin`,
  `end`, `expired`, `superseded`, `refused`; audit `outcome` ∈ `ok`, `error` (§8.10).
- **Source:** **reuse** — `daemon/services/mcps_gateway/store.py:79` `MachineStore` / `:103`
  `roster()`; `daemon/services/mcps_gateway/sessions.py` (session lifecycle);
  `daemon/services/mcps_gateway/tombstones.py` (bounded, owner-scoped tombstone set);
  `daemon/services/mcps_gateway/audit.py` (the only durable surface — the `mcp_gateway_audit`
  table, `daemon/migrations/versions/20261022_000001_create_mcp_gateway_audit.sql:78`).
- **Phase** P1c · **Owner** developer · **Series estimate** 1 + 5 + 1 + 2 = **9**.
- **Degradation:** the gateway is disabled (`config.gateway_enabled`, default **OFF**) ⇒ **no
  series**. The audit table survives restarts; the in-RAM roster and tombstone set do not (§10).

### F19 — Compaction rungs and context occupancy (m6)

- **Name:** `ensemble_compaction_fired_total{rung}` (counter) ·
  `ensemble_compaction_occupancy_ratio{stage}` (gauge) ·
  `ensemble_compaction_precall_estimate_tokens` (gauge). **Type:** counter; gauge; gauge.
  **Labels:** `rung` ∈ `l1_pre_dispatch_80`, `l2_precall_95`, `l3_backstop_600k` (the three-rung
  ladder) · `stage` ∈ `pre_dispatch`, `precall_95`, `post_compaction` (§8.10).
- **Source:** **reuse** — `daemon/compaction.py:1128` `DEFAULT_CONTEXT_LIMIT = 700000` is the
  window the rungs are computed against; the per-instance estimate cache is
  `self._precall_estimates` (`daemon/compaction.py:2299`), a `dict[str, tuple[int, int]]` of
  `(msg_count, total_tokens)`. The rung fires are currently **log-only**
  (`[Compaction][precall-95]`); the counter observes at the same sites. The pre-call gate lives
  in `daemon/graph.py:7616-7670` — the 95 % gate at `:7656-7670` (`PRECALL_COMPACTION_RATIO =
  0.95` at `daemon/graph.py:7497`); note the escalation lowers the ratio to **0.80** when
  proactive-trigger escalation metadata is active, so the metric must record the rung
  **actually applied**.
- **Phase** P1c · **Owner** developer · **Series estimate** 3 + 3 + 1 = **7**.
- **Degradation:** compaction disabled ⇒ **no series** (the rungs never fire).
  `ensemble_compaction_precall_estimate_tokens` renders the **max across instances** — a
  per-instance label would explode cardinality (6 324 instances in prod,
  `daemon/repositories/instance/repository.py:187`).

### F20 — Turn and tool-call seam (m7)

- **Name:** `ensemble_turn_events_total{source}` (counter) · `ensemble_turn_duration_seconds`
  (histogram, buckets §8.11) · `ensemble_tool_calls_total{category}` (counter).
  **Type:** counter; histogram; counter. **Labels:** `source` ∈ the 5 `SOURCE_*` tap constants ·
  `category` ∈ the **37 tool categories** (§8.6, §8.10).
- **Source:** **reuse** — `MessageTapSlot.tap_node_return`
  (`daemon/services/message_tap.py:234`) is the **per-turn seam** (R17). m7: the tool-call and
  turn-performance families instrument **this seam**, NOT graph internals — graph internals would
  multiply measurement sites and re-derive per-turn state the tap already owns. The tool
  category is read from the tool's own registration metadata at the tap site.
- **Phase** P1c · **Owner** developer · **Series estimate** 5 + 14 + 37 = **56** (the summary
  table rounds to 57).
- **Degradation:** the tap is **non-load-bearing** — it swallows every exception and logs at
  WARNING (`daemon/services/message_tap.py:308-309`) — so a tap failure yields **no series**,
  never a broken turn.

### F21 — Journal twin and upgrade pipeline

- **Name:** `ensemble_journal_file_bytes` (gauge) · `ensemble_journal_history_entries` (gauge) ·
  `ensemble_journal_op_seconds` (histogram, buckets §8.11) ·
  `ensemble_journal_ops_total{op}` (counter) · `ensemble_upgrade_phase_seconds{phase}` (gauge) ·
  `ensemble_upgrade_phase_total{phase}` (counter) ·
  `ensemble_upgrade_release_current{version}` (gauge) ·
  `ensemble_upgrade_journal_events_total{kind}` (counter). **Type:** gauge; gauge; histogram;
  counter; gauge; counter; gauge; counter. **Labels:** `op` ∈ **26 journal history event
  types** (§8.7) · `phase` ∈ **10 promote phases** (§8.8) · `kind` ∈ the 3 terminal-class alert
  kinds (§8.8) · `version` ∈ release directory names (`v0.27.12` …), bounded by the retention-3
  release set.
- **Source:** **reuse** — the **Python twin** `daemon/tools/upgrade_journal.py` is the
  instrumented surface: `journal_read` (`:259`), `journal_write` (`:331`), `journal_update_field`
  (`:374`), `journal_history_append` (`:391`), `ensure_extensions` (`:409`).
  **Leader-ratified: NEVER edit `scripts/upgrade/`** — the shell scripts are *observed* (their
  journal events are read through the twin), not modified. `ensemble_journal_file_bytes` =
  `journal_path(install_dir).stat().st_size` (`:255`); `ensemble_journal_history_entries` =
  `len(data["history"])` (the top-level `history` key, `:165`);
  `ensemble_upgrade_release_current{version}` = `data["current"]` (`:159`).
- **Phase** P1c · **Owner** developer (twin), devops (journal-growth alert) · **Series estimate**
  1 + 1 + 13 + 26 + 10 + 10 + 1 + 3 = **65** (the summary table rounds to 66).
- **Degradation:** twin unreachable (no install dir, journal torn) ⇒ **no series**; a torn
  journal raises `JournalTorn` (`:251`) and the twin degrades to the last good copy.

---

## 6. P2 — Exporters and shell-origin series

### F22 — Host, PostgreSQL and shell-process metrics

- **Name:** `ensemble_process_cpu_seconds{name}` (gauge) — shell-origin. Exporter-origin (not
  daemon-origin): node_exporter per-core CPU, per-device disk `await`/`util`, memory, load;
  postgres_exporter `pg_stat_activity` rollup, `pg_stat_statements` top-N, table sizes, WAL,
  autovacuum, `pg_stat_database`. **Type:** gauge; the rest are exporter-native.
  **Labels:** `name` ∈ `launcher`, `promote_sh`, `stage_sh`, `rollback_sh`, `restart_sh`.
- **Source:** **greenfield** — the node_exporter **textfile collector** pattern: the house shell
  scripts rewrite `*.prom` files in a well-known directory under the install dir
  (`$INSTALL_DIR/data/metrics/`). No daemon dependency, crash-safe, and the only way to see the
  **bash** spins (the O(n²) `state.json` walk) that in-daemon metrics cannot. The canonical
  journal shape is `ensemble_journal_file_bytes` / `ensemble_journal_history_entries` (F21) —
  the textfile carries the same numbers so a daemon-down incident still has them.
- **Phase** P2 · **Owner** devops (exporters, textfile dir, dashboards), developer (the textfile
  writer **inside the twin** — never inside `scripts/upgrade/`) · **Series estimate** **5
  shell-origin** (counted separately from the daemon budget) + ≈ **67 exporter-origin** (§1
  excludes both from the ≤2k daemon-origin budget).
- **Degradation:** node_exporter absent ⇒ its series are absent (daemon families unaffected).
  Textfile staleness is detectable by file mtime.

---

## 7. Series roll-up (the ≤2k proof)

| Phase | Families | Daemon-origin series |
|---|---|---|
| P0 | F0.1–F0.4 | 27 |
| P1a | F1–F6 | 62 |
| P1b | F7–F13 | 223 |
| P1c | F14–F21 | 210 |
| **Daemon total** | **F0.1–F21** | **522** |
| Shell-origin (textfile) | F22 | 5 |
| Exporter-origin (node/postgres) | F22 | ≈67 |
| **Grand total on the Prometheus server** | | **≈594** |

**522 daemon-origin series against a ≤2 000 budget — 26 % consumed; headroom 1 478.** The four
largest families (F12 sweeps 69, F21 journal 66, F20 tool calls 57, F9 readiness 53) are each
**bounded, enumerated** label sets, so growth there is bounded by construction. The three
unbounded risks in v1 are explicitly closed: per-instance compaction occupancy is a max; the
leak site is a 6-value fixed map; the MCP roster is capped at 5 values. If every label
combination in the catalog were live at once the total would be ≈1 100 — still inside budget.

---

## 8. Vocabulary annex

Every enumerated label set, with the `file:line` it was verified against.

**8.1 Instance + lifecycle vocabularies** — **Instance status (10)** —
`daemon/repositories/instance/models.py:20-32` (`InstanceStatus`): `idle`, `running`, `waiting`,
`paused`, `completed`, `error`, `terminated`, `queued`, `waiting_children`, `failed`.
**Instance liveness (7)** — `daemon/services/work_status.py:67-99` (`_STATUS_CANONICAL_MAP`):
`pending`, `processing`, `paused`, `completed`, `failed`, `cancelled`, `unknown`; live consumer
`daemon/tools/instance_read.py:228` `_liveness_for`. **Wake-turn outcome (7)**: `claimed`,
`skipped_busy_instance`, `empty_claim`, `timeout_wake`, `no_op_resume`, `fresh_episode_reset`,
`stale_episode`. **Instance lifecycle event (7)**: `spawned`, `revived`, `paused`, `resumed`,
`terminated`, `fan_in_completed`, `fan_in_failed`.

**8.2 Thread group (11)** — `main`, `worker_pool`, `chat_worker_pool`,
`resource_monitor_sampler`, `resource_monitor_flusher`, `resource_monitor_shutdown`,
`wedge_capture_watchdog`, `wedge_capture_shutdown`, `sweep_services`, `llm_stream_watchdog`,
`other`. Sources: `daemon/services/resource_monitor/sampler.py:296`,
`daemon/services/resource_monitor/flusher.py:257`, `daemon/api.py:3425`, `daemon/api.py:2217`,
`daemon/services/wedge_capture/watchdog.py` (`WedgeCaptureWatchdog`, `wedge-capture-shutdown`),
`daemon/services/worker_pool.py:166,207,253` (`Worker`, `lane`),
`daemon/services/stale_task_recovery.py` / `stale_pause_sweep.py` / `report_delivery_recovery.py`
(`StaleTaskRecovery`, `StalePauseSweepService`, `ReportDeliveryRecoveryService`).

**8.3 Readiness + wedge vocabularies** — **Readiness outcome (11)** —
`daemon/services/readiness.py:64-79`: `probe_timeout`, `pool_busy`, `queue_stale`,
`read_timeout`, `boot_incomplete`, `identity_mismatch`, `hard_component_red`, `not_serving`,
`pool_leak`, `unknown`, `ok`. Grace-eligible subset (4): `probe_timeout`, `pool_busy`,
`queue_stale`, `read_timeout` (`:83-89`). Readiness component (4): `database`,
`queue_freshness`, `services`, `checkpoint_saver` (`:94-99`). **Wedge-capture reason (3)** —
`daemon/services/wedge_capture/triggers.py:54-56`: `sustained_cpu`, `sustained_loop_lag`,
`thread_concentration`. **Wedge write outcome (2)**: `ok`, `failed`. **Exposition outcome (2)**:
`ok`, `error`.

**8.4 Sweep lane (13) + outcome (6)** — RDRS lanes (7): `rdrs_deferred`,
`rdrs_no_row_backstop`, `rdrs_pending_age`, `rdrs_recovery_retry`, `rdrs_orphan`,
`rdrs_stuck_wake`, `rdrs_mission_reconciliation`
(`daemon/services/report_delivery_recovery.py:219-266`; kill-switches
`daemon/config.py:1951-2020`). Standalone sweeps (6) + configured interval: `watch_reconcile`
300 s (`daemon/services/watch_reconcile_sweep.py:88`), `eligible_pending` 90 s
(`daemon/services/eligible_pending_sweep.py:67`), `stale_pause` 300 s
(`daemon/services/stale_pause_sweep.py:129`), `orphan_watcher` 90 s
(`daemon/services/orphan_watcher_sweep.py:75`), `job_lock` 90 s
(`daemon/services/job_lock_sweep.py:90`), `upgrade_journal` 90 s
(`daemon/services/upgrade_journal_sweep.py:96`). Sweep row outcome (6): `processed`, `skipped`,
`held`, `released`, `retired`, `error`.

**8.5 MCP roster (5) + churn policy** — `context7`, `plane`, `webfetch` (built-ins,
`daemon/mcp/builtin_servers/`), `custom` (all user-created `mcp_servers` rows,
`daemon/repositories/mcp_server/models.py:17`), `other` (overflow bucket). **Policy:** the three
built-ins are sticky named series; user-created servers are **never** named individually — they
collapse into `custom`; `other` is reserved so the roster can never exceed 5 live values and a
future roster change is additive rather than breaking. MCP state (4): `ok`, `degraded`,
`quarantined`, `unknown` (`daemon/mcp/init_guard.py:145-147`; unknown at
`daemon/routers/mcp_servers.py:302-307`). MCP failure kind (4): `connect`, `initialize`,
`tool_call`, `quarantine`.

**8.6 Tool category (37)** — `attestation`, `bash`, `blueprint`, `board`, `chart`, `context`,
`council`, `db`, `design`, `doc_maintenance`, `filesystem`, `help`, `image`, `infra`,
`instance`, `instance_ops`, `instance_read`, `job`, `knowledge`, `mcp_rpc`, `midflight`,
`mission`, `mother`, `plane_sync`, `proc`, `project`, `question`, `rag`, `scheduling`,
`secrets`, `self`, `service`, `settings`, `shared_meta_kv`, `system`, `system_upgrade`, `time`,
`todo` (the complete set of `register_tool_category(...)` calls under `daemon/tools/`).

**8.7 Journal op (26)** — shell-written (16): `commit`, `halt`, `intent_flip`, `live`,
`quarantine`, `refusal`, `restart`, `restart_gate`, `rollback`, `soak_deferred`,
`soak_grace_extended`, `subprocess_wait_timeout`, `supervision_handback`, `sweep`,
`sweep_rollback`, `warn` (`scripts/upgrade/lib.sh` `journal_history_append` call sites).
Twin-written (10): `arm_notify_no_instance`, `executor_error`, `executor_exit`,
`executor_orphaned`, `executor_still_running`, `nonce_consumed`, `restart_abandoned`,
`supervision_boot`, `wake_abandoned`, `wake_coalesce_overflow`
(`daemon/tools/upgrade_journal.py`, `daemon/services/upgrade_journal_sweep.py`,
`daemon/manager.py:5402`, `daemon/mcp/init_guard.py:561`).

**8.8 Upgrade phase (10) + alert kind (3)** — phases `preflight`, `stop`, `launcher_swap`,
`flip`, `restart`, `livez`, `readyz`, `version`, `soak`, `commit`
(`scripts/upgrade/promote.sh:218,373,387,409,437,445,454,507,516,528`); kinds
`upgrade_cap_halt`, `upgrade_promote_refusal`, `upgrade_auto_rollback`
(`daemon/tools/upgrade_journal.py:645-648`).

**8.9 Pool / PG / checkpoint / spine vocabularies** — pool name (2): `manager`, `checkpoint`;
pool native quantity (4): `size`, `max`, `available`, `overflow`; pool request-error kind (3):
`waiting_exception`, `too_many_requests`, `unclassified`; PG backend state (7): `active`, `idle`,
`idle_in_transaction`, `idle_in_transaction_aborted`, `fastpath_function_call`, `disabled`,
`unknown` (PostgreSQL's `pg_stat_activity.state` domain); PG leak site (6): `instance_read`,
`instance_parent_lookup`, `job_queue_read`, `checkpoint_saver`, `agent_db_tool`, `other`
(§9.2); checkpoint op (4): `aget`, `aput`, `adelete`, `alist`
(`daemon/checkpoint_metrics.py:281-290`); checkpoint table (3): `checkpoints`,
`checkpoint_writes`, `checkpoint_blobs` (`daemon/services/maintenance.py:531-536`); checkpoint
prune outcome (4): `dry_run`, `executed`, `skipped_non_postgres`, `error`; spine table (4):
`resource_daemon_bucket`, `resource_instance_sample`, `resource_pid_map`,
`resource_limit_policy`.

**8.10 Job / LLM / compaction / turn / gateway vocabularies** — admission state (4): `queued`,
`active`, `done`, `dead` (`daemon/repositories/job_queue/models.py:40-43`); worker pool name (2):
`worker`, `chat`; worker pool event (4): `notifications_sent`, `empty_claim_attempts`,
`workers_woken_by_timeout`, `claims_skipped_due_to_busy_instance`
(`daemon/services/worker_pool.py:1381-1386`); LLM outcome (8): §8.12; LLM token direction (2):
`input`, `output` (`daemon/graph.py:3771-3812`); stream-stall phase (3): `abort_attempt`,
`aborted`, `recovered` (`daemon/services/llm_stream_watchdog.py:456,497`); stream-watchdog sweep
outcome (2): `none`, `claimed`; compaction rung (3): `l1_pre_dispatch_80`, `l2_precall_95`,
`l3_backstop_600k` (`daemon/compaction.py:1128`); compaction stage (3): `pre_dispatch`,
`precall_95`, `post_compaction`; turn source (5): `user_message_entry`, `agent_node_return`,
`compaction_aupdate_reactive`, `compaction_aupdate_messaging`, `compaction_precall_95`
(`daemon/services/message_tap.py:120-124`); gateway session outcome (5): `begin`, `end`,
`expired`, `superseded`, `refused`; gateway audit outcome (2): `ok`, `error`.

**8.11 Histogram bucket sets (the seven histograms).** Each is stated once; `_count`/`_sum` are
implied.

| Histogram | Buckets (seconds or bytes) | Series |
|---|---|---|
| `ensemble_checkpoint_op_seconds` | `0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` (**top ≥120 s** — CR-3; the existing `daemon/checkpoint_metrics.py:59-63` set stops at 10.0 and is **extended, not replaced**) | 17 + 2 = 19 / op |
| `ensemble_checkpoint_op_bytes` | `1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216` | 8 + 2 = 10 / op |
| `ensemble_job_claim_latency_seconds` | `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` | 12 + 2 = 14 |
| `ensemble_llm_request_seconds` | `0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0` (plus `1800.0`, `3600.0` reserved for slow models) | 12 + 2 = 14 |
| `ensemble_turn_duration_seconds` | `0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0` | 12 + 2 = 14 |
| `ensemble_journal_op_seconds` | `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0` | 11 + 2 = 13 |
| `ensemble_metrics_exposition_seconds` | `0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0` | 11 + 2 = 13 |

`DEFAULT_LATENCY_BUCKETS` is **not** reused for the LLM or claim-latency histograms — those
ranges are seconds-to-minutes, not milliseconds.

**8.12 The LLM 4-layer retry ladder → the 8-value outcome enum.** The enum in §8.10 is derived
from the real ladder in `daemon/llm_error_classifier.py`, not invented:

| Layer | Mechanism | Source | Outcome value(s) |
|---|---|---|---|
| L1 | tenacity per-category retry — transient and timeout budgets tracked **independently**; timeout checked first because `openai.APITimeoutError` inherits from `APIConnectionError` | `make_llm_retry_strategy` `:764`; closure `counts = {"transient": 0, "timeout": 0, "swapped": False}` `:818`; ordering note `:775-779` | `transient_retry`, `timeout_retry` |
| L2 | failover to the backup endpoint — `base_url` swap when the primary slice is exhausted; caps `PRIMARY_TRANSIENT_MAX = 3`, `PRIMARY_TIMEOUT_MAX = 2` | `FailoverController` `:596`; caps `:574-575` | `failover_to_backup` |
| L3 | reset to primary — the predicate's `attempt_number == 1` branch calls `reset_to_primary()`; sticky-on-success | `:688-692` | `reset_to_primary` |
| L4 | total attempt ceiling — `budget + max(PRIMARY_TRANSIENT_MAX, PRIMARY_TIMEOUT_MAX)` when HA is active; graph.py's `stop_after_attempt` derives from the same constants | `derive_ha_attempt_ceiling` `:578-592` | (bounds L1–L3) |
| — | success | — | `ok` |
| — | budget exhausted (transient class) | — | `terminal_transient` |
| — | non-retryable status (auth, invalid params — blocklist `:110-113`) | — | `terminal_permanent` |
| — | context-length overflow (handled by `daemon/compaction.py`) | — | `context_overflow` |

**8.13 The Carrier-Threading contract for `ensemble_wake_turns_total` (m1).** The wake outcome
is a per-message **flag**, so it follows the blueprint's Carrier-Threading Pattern exactly:

- **Producer** — computed **once**, at the canonical producer: the worker-pool claim path in
  `daemon/services/task_processor.py:569-598` (the same identity that derives
  `is_fresh_episode_user_message`).
- **Carrier** — a new keyword-only field on `ProcessingContext`
  (`daemon/services/message_processing_pipeline.py:121`; the existing carrier field
  `is_fresh_episode_user_message` sits at `:176`), defaulting to `None`.
- **Threading** — keyword-only, `= None` default, threaded pipeline → facade → service, exactly
  as the blueprint's Carrier-Threading Pattern requires.
- **Consumer** — the exposition renderer reads the carried value and increments
  `ensemble_wake_turns_total{outcome=...}`. **Consumer-seam re-derivation is forbidden**: a
  consumer that re-derives the outcome from partial signals drifts from the producer gate (the
  defect class the fresh-episode flag already suffered).
- **Not to be conflated** — the **semantic** consumer of the related fresh-episode flag is
  `daemon/graph.py:7319` `_detect_fresh_episode_reset`.

---

## 9. Census discipline annex (MA-13)

**9.1 The connection.** The census runs on a **dedicated, non-pooled connection** — never
through the P1 manager pool or the P2 checkpoint pool. A probe that consumes pool slots is the
exact failure mode it exists to detect (the readiness-probe amplifier lesson).

| Property | Value | Verified against |
|---|---|---|
| Transport | a direct `psycopg.connect` (no pool) | the wedge-capture's one-shot connect, `daemon/services/wedge_capture/db_capture.py:185` `_connect` |
| `application_name` | `ensemble-metrics-census` | the capture's discipline, `db_capture.py:22-25`, `:62` `CAPTURE_APPLICATION_NAME = "wedge-capture"` |
| `statement_timeout` | set on the census connection (every census query bounded) | `db_capture.py:71-73` |
| `connect_timeout` | set; the DSN is driver-stripped (`db_capture.py:132` `_strip_driver`) | `db_capture.py:29`, `:132` |
| Cadence | **≥15 s** (never faster than the resource-monitor's 15 s floor) | `daemon/config.py:864` `sample_seconds` `ge=15` |
| Self-exclusion | the census **excludes its own connection** from every count (matched on `application_name`) | same reasoning as `db_capture.py:22-25` |
| Failure posture | a census failure renders **no series** and leaves `ensemble_pg_census_last_success_timestamp_seconds` stale — it never raises into the daemon | `db_capture.py`'s "never-raises, degrade one artifact" discipline |

**9.2 Fragment → site map (the fixed site set).** The census attributes an `idle in
transaction` backend to a **site** by matching the backend's `query` fragment against a
**fixed, closed** set of patterns, pinned in the census module (not a config file) so the label
set cannot drift.

| `site` | Matching fragment (pinned pattern) | The real seam it names |
|---|---|---|
| `instance_read` | `SELECT … FROM instance` / `instancehierarchy` | `daemon/repositories/instance/repository.py:414`, `:433`, `:582` (`SQLModelSession` scopes) |
| `instance_parent_lookup` | `SELECT … FROM instance WHERE id =` (single-row get) | `daemon/repositories/instance/repository.py:717-723` `get_parent` |
| `job_queue_read` | `SELECT … FROM job_queue_items` | `daemon/repositories/job_queue/repository.py:337`, `:437`, `:539`, `:555` |
| `checkpoint_saver` | `SELECT … FROM checkpoints` / `checkpoint_blobs` | the psycopg saver pool (`daemon/persistence.py:175-176`); the `aget` path at `daemon/persistence.py:509` |
| `agent_db_tool` | `SELECT` issued by the agent-facing DB tool | `daemon/tools/ens_db_tools.py:1067` (`with Session(repair_engine)`), `:1038` (vacuum engine) |
| `other` | no pattern matched | the residual bucket — **never dropped**, so the census total always equals the sum |

`other` is what makes the metric honest: `sum(ensemble_pg_leaked_txn) ==
ensemble_pg_backends{state="idle_in_transaction"}` must hold at all times. A new leak site lands
in `other` and the residual grows — that is the signal to extend the map (a deliberate, reviewed
change to the closed set).

**9.3 What the census does NOT do.** It never issues a query against the daemon's *own* pools,
never calls `pg_terminate_backend`, never mutates `pg_stat_activity`. Read-only.

**9.4 Relation to the existing leak oracle.** `daemon/services/readiness.py:784`
`_evaluate_leak` arms the `pool_leak` readiness class from **pool counters** (the P2 psycopg
pool's `requests_waiting` and `pool_available` decay), not from `pg_stat_activity`. The census
and the leak oracle are **complementary, not duplicate**: the oracle says "the pool is
misbehaving"; the census says "these N backends have been idle-in-transaction for M seconds, at
these sites". The oracle feeds the readiness composite; the census feeds only `/metrics`.

---

## 10. Cross-restart counters and the alert algebra

**10.1 What resets on restart.** Everything held in RAM resets — that is **every** counter in the
catalog except those read from a durable table.

| Family | Durable? | Note |
|---|---|---|
| F7 `ensemble_checkpoint_*` | **No** — in-RAM registry | resets on restart |
| F10 `ensemble_job_queue_items_created_total` | **Partly** — derived from durable `job_queue_items.created_at` (TEXT ISO) | survives restart |
| F11 `ensemble_worker_pool_*`, `ensemble_workers_busy`, `..._wakeup_efficiency` | **No** — `WorkerPool._stats` is in-RAM | resets on restart |
| F13 `ensemble_wake_turns_total` | **No** | resets on restart |
| F18 `ensemble_mcps_gateway_audit_total` | **Yes** — `mcp_gateway_audit` table | survives restart |
| F21 `ensemble_journal_*`, `ensemble_upgrade_*` | **Yes** — the journal file on disk | survives restart |
| F0.3 `ensemble_resource_spine_rows_written_total` | **Yes** — the resource-monitor tables | survives restart |
| Everything else | **No** | resets on restart |

**10.2 The alert rule.** Any alert over a reset-on-restart counter **must** use `rate()` or
`increase()` — never a raw comparison, never `absent()`. A raw comparison reads "0" right after
a restart and produces a false-negative for the whole fill window. Every v1 alert that touches
a reset-on-restart counter is restated:

- **PoolExhausted** — **rebased** (CR-4):
  `increase(ensemble_db_pool_requests_errors_total[5m]) > 0` (documented as a **superset**, not
  a timeout count). The precise follow-up
  `increase(ensemble_db_pool_requests_errors_total{kind="waiting_exception"}[5m]) > 0` becomes
  available when the checkout seam is instrumented.
- **CheckpointSlow** —
  `histogram_quantile(0.95, rate(ensemble_checkpoint_op_seconds_bucket{op="aget"}[10m])) > 5`
  **paired with** `increase(ensemble_checkpoint_op_seconds_count[10m]) > 0`, so a restart with no
  traffic does not read as "p95 = 0, all good".
- **LoopStarved** — **rewritten** (MA-4): `ensemble_loop_lag_seconds{stat="p95"} > 2`, paired
  with `ensemble_loop_lag_samples > 150` so the alert does not fire on a half-full window.
- **LostWake** — `ensemble_waiting_children_max_age_seconds > 1800` (gauge; read from durable
  rows, survives restarts).
- **SweepHotLoop** —
  `rate(ensemble_sweep_cycles_total{lane="rdrs_mission_reconciliation"}[5m]) * 300 > 2`.
- **JournalWalkRisk** — `ensemble_journal_file_bytes > 200000` (durable).
- **CheckpointBloat** — `ensemble_checkpoint_blobs_table_bytes > 8e9` (durable).
- **PoolLeak** — `ensemble_pg_idle_in_txn_oldest_seconds > 60` (re-established within one
  census cadence).
- **FalseDegraded** —
  `rate(ensemble_readiness_probe_total{outcome="timeout"}[5m]) > 0.1` **and**
  `ensemble_pg_backends{state="active"} < 3`.
- **BashSpin** — `ensemble_process_cpu_seconds{name="promote_sh"} > 0.9` (textfile; durable
  across daemon restarts because the writer is the shell script).

---

## 11. Corrections to the v1 concept and the inventory digest

| # | v1 / digest said | The worktree says | Verified at |
|---|---|---|---|
| C1 | checkpoint buckets `0.001–10.0` ⇒ 17 s / 73–99 s land in `+Inf` | **confirmed** — must extend to ≥120 s | `daemon/checkpoint_metrics.py:59-63` |
| C2 | `checkpoint_metrics.py` lives in `daemon/services/` | **top-level** `daemon/checkpoint_metrics.py` | as cited (355 lines) |
| C3 | `checkpoint_perf.py` lives in `daemon/services/` | **top-level** `daemon/checkpoint_perf.py` | as cited (344 lines) |
| C4 | the metric names are `message_api_*` | **confirmed** — `:274` and `:282`; both re-registered under canonical `ensemble_*` with a one-release alias | `daemon/checkpoint_metrics.py:273-290` |
| C5 | p95 computed at `lag_probe.py:185` | `:185` is `snapshot_histogram()`; p95 is computed at `:115` (p50 `:116`, max `:114`); window `:59` | `daemon/services/wedge_capture/lag_probe.py` |
| C6 | readiness taxonomy at `readiness.py:56-79` | the 11 constants run **`:64-79`**; `:83-89` grace subset; `:94-99` components | `daemon/services/readiness.py` |
| C7 | `llm_error_classifier.py:773+` | **top-level** `daemon/llm_error_classifier.py`; `:773` = `make_llm_retry_strategy` signature, `:818` = closure counters dict | as cited |
| C8 | terminal-chunk undercount caveat at `graph.py:3876-3910` | streaming-usage docstring `:3873-3881`; "stale/undercounting proxy" at **`:7632`** | `daemon/graph.py` |
| C9 | `maintenance.py:533` computes `checkpoint_blobs_table_bytes` verbatim | `:531-536` sums **three** relations; **no** single-table `checkpoint_blobs` size query exists — the metric is a split-out of that one round trip | `daemon/services/maintenance.py:531-536` |
| C10 | wedge-capture reason `concentration` | the constant is **`thread_concentration`** | `daemon/services/wedge_capture/triggers.py:56` |
| C11 | failover bookkeeping is an in-closure dict | **confirmed** — `counts = {"transient": 0, "timeout": 0, "swapped": False}` | `daemon/llm_error_classifier.py:818` |
| C12 | pool-stat honesty vocabulary at `collector.py:24-25` | `:24-26` = `pool_checkout_timeouts` ALWAYS-`None`; `:19-20` derived `checked_out`; `:23` native `requests_waiting`; `:29` `pool_requests_errors` | `daemon/services/wedge_capture/collector.py` |
| C13 | instance statuses = "the actual 10 DB statuses" | **confirmed** — 10 values | `daemon/repositories/instance/models.py:20-32` |
| C14 | sweep lanes = "the actual 7" | the 7 RDRS **lanes** are right, but the `lane` label is **13** (7 RDRS + 6 standalone sweep services) | `daemon/services/report_delivery_recovery.py:219-266` + the six `DEFAULT_*_INTERVAL_SECONDS` |
| C15 | `job_queue_items` has only `created_at` | **confirmed** — TEXT ISO at `:220` and `:357`; no `started_at`/`completed_at` ⇒ durations NOT derivable | `daemon/repositories/job_queue/models.py` |
| C16 | message_queue timestamps are boot-scoped wiped | **confirmed** — `QUEUE_DISCARD_ON_STARTUP` at `daemon/config.py:6429`; columns at `daemon/repositories/message_queue/models.py:83-89` | as cited |
| C17 | `orphan_released` is cumulative | the **docstring** says cumulative; the **code** returns it per-tick (reset at `job_queue_service.py:837`) ⇒ doc drift; the metric accumulates | `daemon/services/job_queue_service.py:828-837` |
| C18 | worker pool has 4 in-RAM counters | **confirmed** | `daemon/services/worker_pool.py:1381-1386` |
| C19 | `instance_fleet_summary` = richest live stats, agent-tool only | **confirmed** — an alias of `fleet_capacity_summary` (`daemon/tools/instance_read.py:1152`), agent-tool only | `daemon/tools/instance_read.py:1152-1164` |
| C20 | `long_tool_nudge.py:955` | the module is `daemon/services/long_tool_nudge.py`, not `daemon/tools/` | `daemon/services/long_tool_nudge.py` |
| C21 | GII throttle dict at `manager.py:1470` | **confirmed** — `self._gii_throttle: dict[str, int] = {}` | `daemon/manager.py:1470` |
| C22 | per-instance tier broken by bool→INTEGER 42804 | **confirmed** — the flusher still binds `bool(...)` at `flusher.py:770`; the audit doc is `daemon/migrations/versions/20261019_120822_alter_resource_monitor_booleans.sql`, the **runtime** fix is `daemon/manager.py:7341` `_ensure_postgres_columns` (PG migration runner is a NO-OP) | as cited |
| C23 | `lag_probe` dark when `ENSEMBLE_WEDGE_CAPTURE_ENABLED` off | **confirmed** — the probe is driven from the watchdog thread | `daemon/services/wedge_capture/lag_probe.py:124-134` |
| C24 | the tool-call family instruments graph internals | **reversed** by m7 — it instruments the `message_tap.py` per-turn seam | `daemon/services/message_tap.py:234` |
| C25 | VictoriaMetrics / Mimir are compatible targets | **dropped** (m9) — only stock Prometheus + Grafana is claimed | — |
| C26 | journal metrics come from bash | **reversed** — they come from the Python twin `daemon/tools/upgrade_journal.py`; `scripts/upgrade/` is read-only, never edited | `daemon/tools/upgrade_journal.py` |

---

## 12. Open items (carried to `decisions.md`)

1. **`ensemble_db_pool_requests_errors_total{kind}` emits only `unclassified` today.** The
   precise split (`waiting_exception` / `too_many_requests`) needs the daemon's own checkout
   seam instrumented. Decide whether that seam lands in P1a or is deferred; the label set is
   forward-compatible either way.
2. **`ensemble_checkpoint_op_bytes` is under-reported for `aget`** until the blob byte count is
   captured at the `log_saver_op` seam (`daemon/checkpoint_perf.py:148`). P1b must either wire
   it or record the metric as "bytes for maintenance carveouts only".
3. **`ensemble_thread_cpu_seconds{thread_group="worker_pool"}` / `chat_worker_pool` need the
   worker threads named** (`daemon/services/worker_pool.py:253`). Until then they fold into
   `other` and the metric is partially blind.
4. **`ensemble_job_claim_latency_seconds` has no existing instrument** (m3). The producer must
   be chosen so the enqueue→claim interval is observed once and carried, not re-derived.
5. **The MCP roster churn policy** (§8.5) collapses user-created servers into `custom`. If
   operators need per-user-server metrics that is a second, explicitly-labelled family — it is
   not smuggled into F17.
6. **`ensemble_sweep_rows_total{lane,outcome}` is per-lane in practice** (most lanes emit 2–3
   of the 6 outcomes). The shared enum is fixed; the live combination count (≈30) is what the
   series estimate uses.
