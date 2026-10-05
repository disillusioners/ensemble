// Snapshot subsystem types — mirrors the BE contract frozen at
//   /api/snapshots, /api/snapshots/{id}, /api/snapshots/metrics
// as defined in `.agents/shared/planning/snapshot-uiux/fe-plan.md` §3
// and the binding reconciliation in `sequencing.md` §1 (D-1..D-7).
//
// The list endpoint returns a LEAN row shape: `task_summary`,
// `warm_spawn_count`, and `project_name` are NOT included in the
// list payload (D-4 / D-5 / sequencing A-2). The detail endpoint
// returns the full `to_dict()` shape MINUS the joined columns —
// `task_summary` IS included on the detail payload (D-4 applies
// only to the list).
//
// The wire-param name for the agent filter is `agent` (D-2); the TS
// interface field is `agent_id` so it matches the row field name.
// `SnapshotFilters` carries the TS name; `SnapshotService.buildParams`
// maps it onto `?agent=` at the wire boundary.

/** Status enum from the BE `SNAPSHOT_STATUSES` allow-list (5 values). */
export type SnapshotStatus =
  | 'active'
  | 'superseded'
  | 'running'
  | 'failed'
  | 'interrupted';

/** Lean row shape returned by `GET /api/snapshots` (per D-4 / D-5 / A-2). */
export interface SnapshotRow {
  id: string;
  project_id: string;
  created_by_agent_id: string;
  target_instance_id: string;
  title: string;
  /** `dim:value` strings (e.g. "domain:api"). */
  domain_tags: string[];
  status: SnapshotStatus;
  supersedes_snapshot_id: string | null;
  git_sha: string | null;
  git_branch: string | null;
  git_dirty: boolean;
  repo_path: string | null;
  runtime_version: string;
  effective_model: string | null;
  /** ISO-8601. */
  created_at: string;
}

/** Envelope returned by `GET /api/snapshots` (D-1 — key is `items`). */
export interface SnapshotListResponse {
  items: SnapshotRow[];
  total: number;
}

/**
 * Detail payload returned by `GET /api/snapshots/{id}`.
 *
 * The detail endpoint returns the full row shape (incl. `task_summary`)
 * plus a `digest` field that is present-but-empty by default and
 * populated only when the request carries `?include=digest` (OK-4).
 * `project_name` and `warm_spawn_count` are NOT in v1 (D-5).
 */
export interface SnapshotDetailResponse extends SnapshotRow {
  task_summary: string;
  /** Always present; empty `{}` unless `?include=digest` was sent. */
  digest: Record<string, unknown>;
}

/** Shape returned by `GET /api/snapshots/metrics`. */
export interface SnapshotUsageMetrics {
  capture_counts: Record<string, { created: number }>;
  spawn_counts_per_snapshot: Array<{ snapshot_id: string; count: number }>;
}

/**
 * Filter shape consumed by `SnapshotService.list()`.
 *
 * NOTE on the agent field (D-2): the TS field name is `agent_id` (to
 * match the row field `created_by_agent_id`); the WIRE param name
 * emitted by `buildParams` is `agent`. The page binds to `agent_id`
 * everywhere; the service does the rename at the wire boundary.
 *
 * Sort enum (D-3): FE renders 4 of the 8 BE allow-list keys. The
 * FE's pre-D-3 `warm_desc` is dropped in v1 (no BE join per D-5).
 *
 * Age (D-7): 4 presets, default `all`. `90d` was dropped from the
 * frozen spec; `all` is the default (not the spec's `30d`).
 */
export interface SnapshotFilters {
  project_id: string | null;
  agent_id: string | null;
  status: SnapshotStatus[];
  tags: string[];
  tag_mode: 'all' | 'any';
  age: '24h' | '7d' | '30d' | 'all';
  sort: 'created_at_desc' | 'created_at_asc' | 'title_asc' | 'status_asc';
  limit: number;
  offset: number;
}
