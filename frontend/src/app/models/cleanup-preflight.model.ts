import { DeferHolderKind } from './defer-blocked.model';

/**
 * Read-only payload returned by GET /api/jobs/cleanup/preflight.
 *
 * The first two counts are always present on the wire. The remaining
 * fields are optional so the UI remains compatible with older daemons
 * while the richer cleanup split rolls out.
 */
export interface CleanupPreflight {
  bad_state_count: number;
  zombie_instance_count: number;
  live_instance_count?: number;
  live_instance_ids?: string[];
  defer_blocked_count?: number;
  /** The holder kind that explains the current defer block, when known. */
  defer_holder_kind?: DeferHolderKind | null;
}

/**
 * Canonical cleanup preflight operator copy (WS4 Round-2 ITEM 3 / T-H1,
 * 2026-09-06 — unblock-round ITEM 5, 2026-09-06 reframe).
 *
 * Single source of truth for the truth-split sentence rendered on the
 * System Cleanup dialog. The same sentence lives in BE
 * (`daemon/routers/jobs_management.py:cleanup_preflight` docstring),
 * the FE dialog template, and `docs/job-task-system.md` §8.5. Editing
 * this constant alone leaves BE/docs out of sync; the plain-TS spec
 * asserts the dialog template renders this const VERBATIM so any
 * template drift fails the suite.
 */
export const CLEANUP_TRUTH_SPLIT_COPY = [
  'Every ACTIVE job is cancelled, together with its whole subtree.',
  'Only missions holding nothing but settled mirrors — no live work',
  '— are kept.',
].join(' ');

/**
 * Return the operator action for the holder kind reported by preflight.
 * Live/unknown holders have no dead-control recommendation: their defer
 * is working as designed, or the daemon is too old to classify it.
 */
export function cleanupDeferNote(
  holderKind: DeferHolderKind | null | undefined
): string | null {
  if (holderKind === 'paused') {
    return 'Paused holder — resume or terminate this holder to unblock.';
  }
  if (holderKind === 'stalled') {
    return 'Stalled holder — force-complete it, or re-send its message in the foreground.';
  }
  return null;
}
