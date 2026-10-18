/**
 * Resource-observability types — a VERBATIM mirror of the FROZEN API
 * contract in `.agents/shared/planning/resource-cost-control/
 * architecture-recommendation.md` §2.8 (D9: the FE consumes the
 * payloads as-is; any perceived drift is escalated to the leader,
 * never normalized away in the client).
 *
 * Timestamps (`t`, `t_start`, `t_end`, `last_ts`, traceback
 * `window.t0/t1`) are NAIVE-UTC wire strings (D10). Consumers must
 * append an explicit `Z` before constructing a `Date` — use
 * `parseNaiveUtc` below. A bare `new Date(str)` parses as local time
 * and skews axis labels / drill-down windows by the session offset.
 */

/** History window selector — frozen wire values. */
export type ResourcesWindow = '15m' | '1h' | '24h' | '7d';

/** Resolution selector — frozen wire values (`auto` = server picks). */
export type ResourcesResolution = 'auto' | 'raw' | 'minute' | 'hour';

/** Detail level reported by the live sampler (§2.9 `detail_level`). */
export type SampleLevel = 'full' | 'reduced' | 'minimal';

/** Anomaly shape classifier (§2.7). */
export type AnomalyShape = 'spike' | 'ramp' | 'leak' | 'step';

/** Deterministic severity banding (§2.7 / P4). */
export type AnomalySeverity = 'low' | 'medium' | 'high';

// ── GET /api/resources/live ─────────────────────────────────────────

export interface LiveDaemon {
  pid: number;
  cpu_pct: number;
  rss_mb: number;
  threads: number;
  fds: number;
  uptime_s: number;
}

export interface LiveCgroupMemory {
  current_mb: number;
  peak_mb: number;
  anon_mb: number;
  file_mb: number;
  slab_mb: number;
}

export interface LiveCgroupCpu {
  usage_usec: number;
  throttled_usec: number;
  nr_throttled: number;
}

export interface LiveCgroup {
  memory: LiveCgroupMemory;
  cpu: LiveCgroupCpu;
}

export interface LiveChild {
  pid: number;
  kind: string;
  name: string;
  cpu_pct: number;
  rss_mb: number;
}

export interface LiveInstance {
  instance_id: string;
  agent: string;
  /** Attribution is partial by physics (§2.2); `estimated` is surfaced honestly. */
  estimated: boolean;
  charged_mb: number;
  child_rss_mb: number;
  children_n: number;
  in_flight_tasks: number;
}

export interface LiveAttribution {
  attributed_mb: number;
  unattributed_mb: number;
  coverage_pct: number;
}

export interface LiveOverruns {
  count: number;
  last_ts: string | null;
}

export interface SelfCostFlush {
  batches: number;
  bytes: number;
  errors: number;
}

export interface SelfCostProfiling {
  active: boolean;
  capabilities: string[];
}

/** `GET /api/resources/self-cost` payload (also embedded in `live.self_cost`). */
export interface SelfCostPayload {
  cpu_pct: number;
  cumulative_cpu_s: number;
  buffers_bytes: number;
  rss_budget_mb: number;
  threads: number;
  samples: number;
  ticks_skipped: number;
  flush: SelfCostFlush;
  profiling: SelfCostProfiling;
}

export interface LivePayload {
  ts: string;
  sample_level: SampleLevel;
  daemon: LiveDaemon;
  cgroup: LiveCgroup;
  children: LiveChild[];
  instances: LiveInstance[];
  attribution: LiveAttribution;
  overruns: LiveOverruns;
  self_cost: SelfCostPayload;
}

// ── GET /api/resources/history ──────────────────────────────────────

export interface HistoryPoint {
  t: string;
  daemon_cpu: number;
  daemon_rss_mb: number;
  cgroup_mem_mb: number;
  threads: number;
  instances_active: number;
}

export interface HistoryPayload {
  points: HistoryPoint[];
  /** Server-chosen resolution after auto-fall (`raw | minute | hour`). */
  resolution_used: Exclude<ResourcesResolution, 'auto'>;
  rollup: string;
  /** True when the bucket series was rolled up / capped. Rendered as-is. */
  truncated: boolean;
}

// ── GET /api/resources/anomalies ────────────────────────────────────

export interface Anomaly {
  id: string;
  kind: string;
  t_start: string;
  t_end: string;
  peak: number;
  baseline: number;
  severity: AnomalySeverity;
  series_key: string;
  shape: AnomalyShape;
  instance_id?: string;
}

export interface AnomaliesPayload {
  anomalies: Anomaly[];
}

// ── GET /api/resources/anomalies/{id}/evidence ──────────────────────

/**
 * Contributor row per §2.7's explainability payload
 * (`{instance_id, agent_id, work_id?, share_pct, evidence:{…}}`).
 * `share_pct` sums to ≤100; the explicit `unattributed` remainder row
 * carries the honesty gap (§2.2 / §2.7).
 */
export interface EvidenceTaskStatus {
  status: string;
  worker_id?: string | null;
}

export interface EvidenceContributorEvidence {
  samples?: unknown[];
  first_seen?: string;
  task_status?: EvidenceTaskStatus;
}

export interface EvidenceContributor {
  instance_id?: string;
  agent_id?: string;
  work_id?: string | null;
  share_pct: number;
  evidence?: EvidenceContributorEvidence;
  /** Explicit remainder row — attribution is partial by physics. */
  unattributed?: boolean;
}

export interface EvidencePayload {
  anomaly: Anomaly;
  samples: unknown[];
  top_pids_per_bucket: unknown[];
  children_delta: unknown[];
  contributors: EvidenceContributor[];
}

// ── GET /api/resources/traceback?t0=&t1= ────────────────────────────

export interface TracebackWindow {
  t0: string;
  t1: string;
}

export interface TracebackInstanceAlive {
  instance_id: string;
  agent?: string;
  status?: string;
}

export interface TracebackInFlightTask {
  task_id?: string;
  work_id?: string;
  instance_id?: string;
  status?: string;
  created_at?: string;
}

export interface TracebackSpawnedChild {
  pid?: number;
  /** `chart_render` is the 8.3 GB-class signal — highlighted in the UI. */
  kind: string;
  name?: string;
  spawned_at?: string;
  exited_at?: string | null;
  instance_id?: string;
}

/** Structural counters — best-effort; `null` when unavailable mid-boot (§P3 R5). */
export interface TracebackStructural {
  queue_depth: number | null;
  workers: number | null;
  checkpoints: number | null;
  gc_events: number | null;
}

export interface TracebackPayload {
  window: TracebackWindow;
  instances_alive: TracebackInstanceAlive[];
  in_flight_tasks: TracebackInFlightTask[];
  spawned_children: TracebackSpawnedChild[];
  structural: TracebackStructural;
  findings: unknown[];
}

// ── Pure helpers ────────────────────────────────────────────────────

/**
 * D10 — parse a naive-UTC wire timestamp. Adds an explicit `Z` unless
 * the string already carries a zone designator/offset. NEVER call
 * `new Date(str)` directly on the §2.8 payload timestamps.
 */
export function parseNaiveUtc(value: string): Date {
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value)) {
    return new Date(value);
  }
  return new Date(`${value}Z`);
}

/** Format a naive-UTC wire timestamp as an explicit UTC clock label. */
export function formatUtcLabel(value: string): string {
  const d = parseNaiveUtc(value);
  if (Number.isNaN(d.getTime())) {
    return value;
  }
  return d.toISOString().slice(11, 16);
}
