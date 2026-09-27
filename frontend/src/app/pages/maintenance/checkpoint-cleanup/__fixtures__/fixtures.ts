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