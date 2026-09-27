/**
 * Shared fixtures for the Checkpoint Cleanup spec files
 * (`checkpoint-cleanup.component.spec.ts` + `checkpoint-cleanup.service.spec.ts`).
 *
 * Item 10 — DRY the spec surface. Both spec files previously carried
 * near-identical copies of `DRY_RUN`, `EXECUTE_RESP`, `RUN_*`, and the
 * `268435456` (= 256 MB binary) literal. Drift between the two specs
 * is no longer possible; the canonical fixture lives here.
 *
 * The fixtures use the CANONICAL type contracts from
 * `../../../models` (no local type shadows — Item 15). They are pure
 * data (no behavior); the spec bodies own the assertions.
 */

import type {
  CheckpointCleanupBlobsSummary,
  CheckpointCleanupDryRun,
  CheckpointCleanupExecute,
  CheckpointCleanupLastRun,
  CheckpointCleanupRun,
  CheckpointCleanupStatus,
  MaintenanceAvailability,
} from '../../../../models';

/** Binary 256 MB — pinned literal across all 3 specs (binary 1024^3). */
export const BYTES_256_MB = 268435456;

/** ISO timestamp helper — fresh_until 5 min from now (used by DRY_RUN). */
const FRESH_FUTURE_ISO = (offsetMs: number = 5 * 60 * 1000): string =>
  new Date(Date.now() + offsetMs).toISOString();

/** ISO timestamp helper — fresh_until 1 min in the past (used by STALE_DRY_RUN). */
const STALE_PAST_ISO = (offsetMs: number = -60_000): string =>
  new Date(Date.now() + offsetMs).toISOString();

/** Standard dry-run body used by both component + service specs. */
export const DRY_RUN: CheckpointCleanupDryRun = {
  run_id: 'ckpt-20260927_032000123456-2a18f3c9',
  would_delete: { checkpoint_rows: 2, writes: 0, blobs: 4, bytes: BYTES_256_MB },
  would_delete_count: 4,
  would_free_bytes: BYTES_256_MB,
  scanned: { thread_ns_pairs: 12 },
  skipped: [
    { thread_id: 'thr-1', checkpoint_ns: '', reason: 'ZERO_REFS_FAIL_SAFE' },
    { thread_id: 'thr-2', checkpoint_ns: 'snap:x', reason: 'MAX_REFS_EXCEEDED' },
    { thread_id: 'thr-3', checkpoint_ns: '', reason: 'ERROR:MyException' },
  ],
  skipped_truncated: false,
  duration_ms: 412,
  fresh_until: FRESH_FUTURE_ISO(),
};

/** Stale variant — fresh_until 1 min in the past. */
export const STALE_DRY_RUN: CheckpointCleanupDryRun = {
  ...DRY_RUN,
  fresh_until: STALE_PAST_ISO(),
};

/**
 * v3.2 — never-pruned DB projection fixture (the incident scenario).
 * Current orphans = 0 (retention never ran); excess rows still to
 * delete = 104,501; blobs-only-referenced-by-excess-rows = 11 GB.
 *
 *   now  = 0           (pass 1 frees nothing — Op D orphans the
 *                       blobs that pass 2's E reclaims)
 *   after = 11,811,060,000   (~11 GB — what a follow-up run frees)
 *   total = 11,811,060,000   (informational sum)
 *
 * Drives the "On a DB that has never run retention, run 1 deletes
 * rows only; run 2 frees the blob bytes" sub-copy.
 */
export const DRY_RUN_NEVER_PRUNED: CheckpointCleanupDryRun = {
  run_id: 'ckpt-20260928_never-pruned-f1a2b3c4',
  would_delete: {
    checkpoint_rows: 104501,
    writes: 273293,
    blobs: 0,
    bytes: 0,
  },
  would_delete_count: 0,
  would_free_bytes: 0,
  bytes_reclaimable_now: 0,
  bytes_reclaimable_after_row_prune: 11811060000,
  bytes_reclaimable_total: 11811060000,
  scanned: { thread_ns_pairs: 412 },
  skipped: [],
  skipped_truncated: false,
  duration_ms: 412,
  fresh_until: FRESH_FUTURE_ISO(),
};

/**
 * v3.2 — fully-pruned DB projection fixture (post-pass on the
 * previously-never-pruned DB). All blobs already orphaned by an
 * earlier pass; Op D has nothing left to delete; Op E deletes
 * everything in this single pass. Models the "pass 2 / convergence"
 * state.
 *
 *   now   = 256 MB     (pass-2 E frees what pass 1's D orphaned)
 *   after = 0          (no further referencers to delete)
 *   total = 256 MB
 *
 * Drives the case where the run-again banner must HIDE — a fresh
 * dry-run reports `bytes_reclaimable_now == 0` after a successful
 * execute against `DRY_RUN_NEVER_PRUNED`.
 */
export const DRY_RUN_PRUNED: CheckpointCleanupDryRun = {
  ...DRY_RUN,
  bytes_reclaimable_now: BYTES_256_MB,
  bytes_reclaimable_after_row_prune: 0,
  bytes_reclaimable_total: BYTES_256_MB,
};

/**
 * v3.2 — mixed projection fixture. Both runs free SOMETHING —
 * represents a steady-state DB where new orphaned blobs accumulate
 * alongside excess rows. Used to verify the three-number render
 * formats every non-zero component (no "—" substitutions).
 */
export const DRY_RUN_MIXED: CheckpointCleanupDryRun = {
  run_id: 'ckpt-20260928_mixed-projection-c5d6e7f8',
  would_delete: {
    checkpoint_rows: 5000,
    writes: 12000,
    blobs: 50,
    bytes: 104857600, // 100 MB
  },
  would_delete_count: 50,
  would_free_bytes: 104857600,
  bytes_reclaimable_now: 104857600,
  bytes_reclaimable_after_row_prune: 5368709120, // 5 GB
  bytes_reclaimable_total: 5473566720,
  scanned: { thread_ns_pairs: 88 },
  skipped: [],
  skipped_truncated: false,
  duration_ms: 218,
  fresh_until: FRESH_FUTURE_ISO(),
};

/**
 * v3.2 — projection fixture with a non-empty `skipped[]`.
 * Drives the R-4 "skipped-flag" banner — pairs over the
 * `CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD` cap contribute 0 to
 * the projection AND surface in `skipped[]`. The FE MUST render
 * "N pairs skipped — cleanup effectiveness may be understated"
 * near the projection (R-4 honesty gap).
 */
export const DRY_RUN_WITH_SKIPPED: CheckpointCleanupDryRun = {
  run_id: 'ckpt-20260928_with-skipped-9a8b7c6d',
  would_delete: {
    checkpoint_rows: 1200,
    writes: 3400,
    blobs: 0,
    bytes: 0,
  },
  would_delete_count: 0,
  would_free_bytes: 0,
  bytes_reclaimable_now: 0,
  bytes_reclaimable_after_row_prune: 5368709120, // 5 GB
  bytes_reclaimable_total: 5368709120,
  scanned: { thread_ns_pairs: 24 },
  skipped: [
    { thread_id: 'thr-cap-1', checkpoint_ns: '', reason: 'MAX_REFS_EXCEEDED' },
    { thread_id: 'thr-cap-2', checkpoint_ns: '', reason: 'MAX_REFS_EXCEEDED' },
    { thread_id: 'thr-failsafe', checkpoint_ns: '', reason: 'ZERO_REFS_FAIL_SAFE' },
  ],
  skipped_truncated: false,
  duration_ms: 96,
  fresh_until: FRESH_FUTURE_ISO(),
};

/** Standard 202 body from POST /execute. */
export const EXECUTE_RESP: CheckpointCleanupExecute = {
  run_id: 'ckpt-20260927_032130456789-7e11f3a2',
  status: 'running',
  started_at: '2026-09-27T03:21:30.456789+00:00',
  advisory: null,
  expected_duration_ms_hint: 412,
};

/** Standard status body. */
export const STATUS: CheckpointCleanupStatus = {
  config: {
    checkpoint_max_per_thread: 3,
    checkpoint_max_per_thread_floor: 1,
    cleanup_interval_hours: 24,
    blob_prune_dry_run_env_default: '1',
    blob_prune_destructive_armed: false,
  },
  last_run: null,
  in_flight: null,
};

/** Standard availability body — gear-menu probe target. */
export const AVAILABILITY_READY: MaintenanceAvailability = {
  eligible: true,
  state: 'ready',
  backend: 'postgres',
  reason: null,
};

/** Dry-flavor blob summary. */
export const BLOBS_DRY: CheckpointCleanupBlobsSummary = {
  scanned_pairs: 12,
  would_delete_count: 4,
  would_free_bytes: BYTES_256_MB,
  would_delete: 4,
  bytes: BYTES_256_MB,
  destructive: false,
  skipped: [],
  skipped_truncated: false,
};

/** Destructive-flavor blob summary. */
export const BLOBS_DESTRUCTIVE: CheckpointCleanupBlobsSummary = {
  scanned_pairs: 12,
  would_delete_count: 0,
  would_free_bytes: 0,
  would_delete: 0,
  bytes: 0,
  destructive: true,
  deleted: 4,
  bytes_freed: BYTES_256_MB,
  skipped: [],
  skipped_truncated: false,
};

/** Build a run at the requested status. */
export function makeRun(status: CheckpointCleanupRun['status']): CheckpointCleanupRun {
  return {
    run_id: EXECUTE_RESP.run_id,
    kind: 'manual_execute',
    status,
    started_at: EXECUTE_RESP.started_at,
    completed_at: status === 'running' ? null : new Date().toISOString(),
    summary: status === 'running' ? null : {
      checkpoint_rows: { scanned_pairs: 12, deleted: 4, excess_pairs: 4 },
      writes: { deleted: 0 },
      blobs: BLOBS_DRY,
      duration_ms: 1823,
    },
    error: status === 'interrupted' ? { code: 'run_interrupted', message: 'daemon restart' } : null,
  };
}

/**
 * v3.2 — succeeded manual_execute run summary with the source
 * dry-run's projection echoed onto the run row. Echoes the
 * never-pruned dry-run: now=0 / after=11.8 GB / total=11.8 GB —
 * so the post-run banner MUST render "Run cleanup again to reclaim
 * ~11 GB more" once the run succeeds.
 */
export const RUN_SUCCEEDED_NEVER_PRUNED: CheckpointCleanupRun = {
  run_id: EXECUTE_RESP.run_id,
  kind: 'manual_execute',
  status: 'succeeded',
  started_at: EXECUTE_RESP.started_at,
  completed_at: '2026-09-28T11:01:42.123456+00:00',
  summary: {
    checkpoint_rows: { scanned_pairs: 412, deleted: 104501, excess_pairs: 412 },
    writes: { deleted: 273293 },
    // The destructive-flavor blobs summary: 0 blobs freed THIS run
    // (Op D orphaned them for pass 2).
    blobs: {
      scanned_pairs: 412,
      would_delete_count: 0,
      would_free_bytes: 0,
      would_delete: 0,
      bytes: 0,
      destructive: true,
      deleted: 0,
      bytes_freed: 0,
      skipped: [],
      skipped_truncated: false,
    },
    duration_ms: 1823456,
    // v3.2 — the additive projection block echoing the dry-run
    // that sourced this execute. Powers the run-again banner.
    projection: {
      bytes_reclaimable_now_at_dry_run: 0,
      bytes_reclaimable_after_row_prune_at_dry_run: 11811060000,
    },
  },
  error: null,
};

/**
 * v3.2 — fully-pruned converged run. after=0 → banner HIDES.
 * Models the post-banner state: a fresh dry-run after the execute
 * confirms nothing remains to reclaim.
 */
export const RUN_SUCCEEDED_PRUNED: CheckpointCleanupRun = {
  ...RUN_SUCCEEDED_NEVER_PRUNED,
  run_id: 'ckpt-20260928_pruned-succeed-1',
  summary: {
    ...RUN_SUCCEEDED_NEVER_PRUNED.summary!,
    projection: {
      bytes_reclaimable_now_at_dry_run: BYTES_256_MB,
      bytes_reclaimable_after_row_prune_at_dry_run: 0,
    },
  },
};

/**
 * v3.2 — auto-cycle run (R-5: `projection` absent on auto rows).
 * Drives the negative arm: `showRunAgainBanner(...)` returns false
 * on a run whose summary has no projection block.
 */
export const RUN_AUTO_NO_PROJECTION: CheckpointCleanupRun = {
  run_id: 'ckpt-20260928_auto-cycle-1',
  kind: 'auto',
  status: 'succeeded',
  started_at: '2026-09-28T05:00:00.000+00:00',
  completed_at: '2026-09-28T05:02:18.000+00:00',
  summary: {
    checkpoint_rows: { scanned_pairs: 50, deleted: 12, excess_pairs: 12 },
    writes: { deleted: 4 },
    blobs: {
      scanned_pairs: 50,
      would_delete_count: 0,
      would_free_bytes: 0,
      would_delete: 0,
      bytes: 0,
      destructive: true,
      deleted: 8,
      bytes_freed: 134217728, // 128 MB
      skipped: [],
      skipped_truncated: false,
    },
    duration_ms: 138000,
    // No `projection` block — auto rows (R-5).
  },
  error: null,
};

/**
 * v3.2 — failed run with a non-null summary. Drives the negative
 * arm: `showRunAgainBanner(...)` MUST return false on failed /
 * interrupted / running statuses — only `succeeded` is banner-eligible.
 */
export const RUN_FAILED_NEVER_PRUNED: CheckpointCleanupRun = {
  ...RUN_SUCCEEDED_NEVER_PRUNED,
  run_id: 'ckpt-20260928_failed-1',
  status: 'failed',
  completed_at: '2026-09-28T11:00:30.000+00:00',
  summary: {
    ...RUN_SUCCEEDED_NEVER_PRUNED.summary!,
  },
  error: { code: 'internal_error', message: 'something blew up' },
};

/**
 * v3.2 — interrupted run. Drives the negative arm: banner hides.
 */
export const RUN_INTERRUPTED_NEVER_PRUNED: CheckpointCleanupRun = {
  ...RUN_SUCCEEDED_NEVER_PRUNED,
  run_id: 'ckpt-20260928_interrupted-1',
  status: 'interrupted',
  completed_at: '2026-09-28T11:00:15.000+00:00',
  error: { code: 'run_interrupted', message: 'daemon restart' },
};

/**
 * v3.2 B2 — `status.last_run` shaped row that seeds the post-run
 * banner on page load (refresh persistence). Mirrors
 * `RUN_SUCCEEDED_NEVER_PRUNED` (same source projection: never-pruned,
 * after = 11.8 GB) but in the wire-shape `CheckpointCleanupLastRun`
 * (`error` field absent; status narrowed to 'succeeded' | 'failed').
 * Drives the B2 spec: page load with this row → `refreshStatus()`
 * seeds `lastExecuteResult` → banner visible WITHOUT any client action.
 */
export const STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED: CheckpointCleanupLastRun = {
  run_id: RUN_SUCCEEDED_NEVER_PRUNED.run_id,
  kind: 'manual_execute',
  started_at: RUN_SUCCEEDED_NEVER_PRUNED.started_at,
  completed_at: RUN_SUCCEEDED_NEVER_PRUNED.completed_at,
  status: 'succeeded',
  summary: RUN_SUCCEEDED_NEVER_PRUNED.summary!,
};

/**
 * v3.2 B2 — auto-cycle last_run. Drives the negative arm: B2
 * seeding skips auto rows because R-5 (auto rows have no
 * `projection` block). Without `after > 0` the banner must NOT
 * seed.
 */
export const STATUS_LAST_RUN_AUTO_SUCCEEDED: CheckpointCleanupLastRun = {
  run_id: RUN_AUTO_NO_PROJECTION.run_id,
  kind: 'auto',
  started_at: RUN_AUTO_NO_PROJECTION.started_at,
  completed_at: RUN_AUTO_NO_PROJECTION.completed_at,
  status: 'succeeded',
  summary: {
    // Same shape as RUN_AUTO_NO_PROJECTION.summary but stripped
    // of the absent `projection` (auto rows omit it).
    checkpoint_rows: { scanned_pairs: 50, deleted: 12, excess_pairs: 12 },
    writes: { deleted: 4 },
    blobs: {
      scanned_pairs: 50,
      would_delete_count: 0,
      would_free_bytes: 0,
      would_delete: 0,
      bytes: 0,
      destructive: true,
      deleted: 8,
      bytes_freed: 134217728, // 128 MB
      skipped: [],
      skipped_truncated: false,
    },
    duration_ms: 138000,
    // No `projection` — auto rows.
  },
};

/**
 * v3.2 B2 — manual_execute last_run with projection.after === 0.
 * Drives the negative arm: even on `kind: 'manual_execute'`, a
 * zero after value means nothing more to reclaim — banner MUST NOT
 * seed (matches `RUN_SUCCEEDED_PRUNED` semantics).
 */
export const STATUS_LAST_RUN_SUCCEEDED_AFTER_ZERO: CheckpointCleanupLastRun = {
  ...STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
  run_id: 'ckpt-20260928_pruned-last-run-1',
  summary: {
    ...STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED.summary,
    projection: {
      bytes_reclaimable_now_at_dry_run: 268435456,
      bytes_reclaimable_after_row_prune_at_dry_run: 0,
    },
  },
};