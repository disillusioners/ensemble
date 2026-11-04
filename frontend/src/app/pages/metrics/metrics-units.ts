/**
 * The METRIC → UNIT VOCABULARY (T1 phase 4, task 4.15 — the mechanism;
 * phase 6, task 6.3 — the CONTENT).
 *
 * ── WHY A VOCABULARY AT ALL ─────────────────────────────────────────────
 *
 * A panel is composed per **metric-kind + unit group** (IMPL-10, R-1).
 * The KIND comes from the wire (`chart_role` per metric); the UNIT
 * cannot — the door's families carry `labels` and `role`, never units.
 * So the unit is an FE-LOCAL, TYPED table keyed on the METRIC NAME
 * (never on the family: a family mixes kinds and units).
 *
 * **A wrong unit is worse than no unit.** A metric with no vocabulary
 * entry renders `unit unknown` — never a guess, never a borrowed unit
 * from a sibling metric. Adding a genuinely new metric name is
 * therefore a ONE-LINE vocabulary edit, not a code change (the
 * manifest's registration-only contract).
 *
 * ── THE CONTENT (phase 6, task 6.3) ─────────────────────────────────────
 *
 * Every entry below is DERIVED from a READ-ONLY daemon code fact — the
 * metric registration in `daemon/services/metrics_query/wiring/` (the
 * metric `name` and its help) — never invented and never inferred from
 * the name shape. The inference ban is a SPEC rule (IMPL-10 (iii)):
 * an unmapped metric renders `unit unknown` even when its name ends in
 * a unit-looking suffix.
 *
 * Units label the value AS SERVED (the FE plots values verbatim, no
 * conversion): `_seconds` metrics serve seconds (`s`), `_bytes`
 * metrics serve bytes (`bytes`), counts serve `count`, the resource
 * timeline's own series serve MB / % as the resources API names them.
 * A 0/1 gauge is a `flag`; an enumerated state code is a `state`; a
 * 0–1 share is a `ratio`. No unit is claimed for anything the daemon
 * does not state.
 */

/** The placeholder a metric with no vocabulary entry renders. */
export const METRICS_UNIT_UNKNOWN = 'unit unknown';

/**
 * The metric-name → unit table.
 *
 * The key is the METRIC NAME — the same string the door serves in
 * `MetricsFamilyName.name`.
 *
 * Provenance per entry (the wiring file is READ-ONLY reference):
 *
 * ── F1 the event-loop lag probe (`w1.py`) ──
 * `ensemble_loop_lag_seconds`            → s      (quantiles "in seconds")
 * `ensemble_loop_lag_samples`            → count  (the rolling-window sample count)
 * `ensemble_loop_lag_overflow_total`     → count  (counter)
 * `ensemble_loop_lag_probe_state`        → flag   ("1 for the current state, 0 for the others")
 * ── F3 the wedge-capture counters (`w1.py`) ──
 * `ensemble_wedge_capture_total`         → count
 * `ensemble_wedge_capture_write_total`   → count
 * ── F4 the db pool counters (`w1.py:194-228` — the gauge+counter family) ──
 * `ensemble_db_pool_native`              → count  (size / max / available / overflow)
 * `ensemble_db_pool_requests_errors_total` → count (counter)
 * ── F5 the db pool gauges (`w1.py`) ──
 * `ensemble_db_pool_checked_out`         → count  ("pool_size - pool_available")
 * `ensemble_db_pool_waiting_clients`     → count
 * ── F6 the PostgreSQL census (`w1.py`) ──
 * `ensemble_pg_backends`                 → count
 * `ensemble_pg_idle_in_txn_oldest_seconds` → s
 * `ensemble_pg_leaked_txn`               → count
 * `ensemble_pg_census_last_success_timestamp_seconds` → s (a wall-clock stamp in seconds)
 * ── F8 the checkpoint table bytes (`w2.py`) ──
 * `ensemble_checkpoint_blobs_table_bytes` → bytes
 * `ensemble_checkpoint_table_bytes`       → bytes
 * ── F9 the readiness composite (`w2.py:120-150` — the counter + 0/1-gauge family) ──
 * `ensemble_readiness_probe_total`       → count
 * `ensemble_readiness_grace_active`      → flag   (a 0/1 gauge)
 * `ensemble_readiness_failure_streak`    → count
 * `ensemble_readiness_component_state`   → flag   ("1 healthy, 0 degraded")
 * ── F10 the job queue (`w2.py`) ──
 * `ensemble_job_queue_depth`             → count
 * `ensemble_dead_letter_items`           → count
 * `ensemble_job_queue_items_created_total` → count
 * ── F11 the worker pool (`w2.py`) ──
 * `ensemble_workers_total`               → count
 * `ensemble_workers_busy`                → count
 * `ensemble_worker_pool_wakeup_efficiency` → ratio ("notifications / (notifications + empty claims)")
 * `ensemble_worker_pool_events_total`    → count
 * `ensemble_task_heartbeat_age_seconds`  → s
 * `ensemble_task_heartbeat_cadence_seconds` → s (the CONFIGURED cadence)
 * ── F12 the sweep counters (`w2.py`) ──
 * `ensemble_sweep_cycles_total`          → count
 * `ensemble_sweep_errors_total`          → count
 * `ensemble_sweep_rows_total`            → count
 * ── F13 the instance counts (`w2.py`) ──
 * `ensemble_instance_status_count`       → count
 * `ensemble_instance_liveness_count`     → count
 * `ensemble_waiting_children_count`      → count
 * `ensemble_waiting_children_max_age_seconds` → s
 * ── F17 the MCP server health (`w3.py`) ──
 * `ensemble_mcp_server_state`            → state  (an enumerated health-state code)
 * `ensemble_mcp_failures_total`          → count
 * ── the resource timeline's OWN series (`timeline-series.ts`; served by
 *    the resources API, not the door) ──
 * `daemon_rss_mb`                        → MB
 * `cgroup_mem_mb`                        → MB
 * `daemon_cpu`                           → %
 * `threads`                              → count
 * `instances_active`                     → count
 */
export const METRICS_UNIT_VOCABULARY: Readonly<Record<string, string>> = {
  // F1 — the event-loop lag probe
  ensemble_loop_lag_seconds: 's',
  ensemble_loop_lag_samples: 'count',
  ensemble_loop_lag_overflow_total: 'count',
  ensemble_loop_lag_probe_state: 'flag',
  // F3 — the wedge-capture counters
  ensemble_wedge_capture_total: 'count',
  ensemble_wedge_capture_write_total: 'count',
  // F4 — the db pool (the gauge + counter family, w1.py:194-228)
  ensemble_db_pool_native: 'count',
  ensemble_db_pool_requests_errors_total: 'count',
  // F5 — the db pool gauges
  ensemble_db_pool_checked_out: 'count',
  ensemble_db_pool_waiting_clients: 'count',
  // F6 — the PostgreSQL census
  ensemble_pg_backends: 'count',
  ensemble_pg_idle_in_txn_oldest_seconds: 's',
  ensemble_pg_leaked_txn: 'count',
  ensemble_pg_census_last_success_timestamp_seconds: 's',
  // F8 — the checkpoint table bytes
  ensemble_checkpoint_blobs_table_bytes: 'bytes',
  ensemble_checkpoint_table_bytes: 'bytes',
  // F9 — readiness (the counter + 0/1-gauge family, w2.py:120-150)
  ensemble_readiness_probe_total: 'count',
  ensemble_readiness_grace_active: 'flag',
  ensemble_readiness_failure_streak: 'count',
  ensemble_readiness_component_state: 'flag',
  // F10 — the job queue
  ensemble_job_queue_depth: 'count',
  ensemble_dead_letter_items: 'count',
  ensemble_job_queue_items_created_total: 'count',
  // F11 — the worker pool
  ensemble_workers_total: 'count',
  ensemble_workers_busy: 'count',
  ensemble_worker_pool_wakeup_efficiency: 'ratio',
  ensemble_worker_pool_events_total: 'count',
  ensemble_task_heartbeat_age_seconds: 's',
  ensemble_task_heartbeat_cadence_seconds: 's',
  // F12 — the sweep counters
  ensemble_sweep_cycles_total: 'count',
  ensemble_sweep_errors_total: 'count',
  ensemble_sweep_rows_total: 'count',
  // F13 — the instance counts
  ensemble_instance_status_count: 'count',
  ensemble_instance_liveness_count: 'count',
  ensemble_waiting_children_count: 'count',
  ensemble_waiting_children_max_age_seconds: 's',
  // F17 — the MCP server health
  ensemble_mcp_server_state: 'state',
  ensemble_mcp_failures_total: 'count',
  // The resource timeline's own series (the resources API's names)
  daemon_rss_mb: 'MB',
  cgroup_mem_mb: 'MB',
  daemon_cpu: '%',
  threads: 'count',
  instances_active: 'count',
};

/**
 * The unit of a metric name.
 *
 * An unknown name renders `unit unknown` — never a guess (IMPL-10 (iii)).
 */
export function metricsUnitFor(
  metricName: string,
  vocabulary: Readonly<Record<string, string>> = METRICS_UNIT_VOCABULARY,
): string {
  const unit = vocabulary[metricName];
  return unit === undefined || unit === '' ? METRICS_UNIT_UNKNOWN : unit;
}

/**
 * The panel GROUP KEY — the metric-kind + unit pair.
 *
 * Two series share a panel only when BOTH their kind and their unit
 * match: a mixed-kind panel would break counter-honesty (a counter
 * rendered LINEAR asserts values that were never observed) and a
 * mixed-unit panel would falsify the unit label.
 */
export function metricsPanelGroupKey(kind: string, unit: string): string {
  return `${kind}::${unit}`;
}
