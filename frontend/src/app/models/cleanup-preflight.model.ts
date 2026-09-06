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
