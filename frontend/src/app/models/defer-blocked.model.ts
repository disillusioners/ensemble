// Defer-blocked warning model — FE counterpart of
// ``GET /api/queues/defer-blocked``.
//
// The defer gate holds pending defer jobs while holder instances
// (paused or live) own the deferral. The header badge surfaces a
// small severity-coded warning affordance derived from the payload.
// All derivation lives in this pure helper (house convention:
// components stay thin computeds over model helpers; logic-mirror
// specs prove the helper, not the component).

/** Kind of the instance holding the defer block. */
export type DeferHolderKind = 'paused' | 'live';

/** One holder instance from the defer-blocked payload. */
export interface DeferBlockHolder {
  instance_id: string;
  agent: string;
  status: string;
  /**
   * When the hold started — ISO-8601, +00:00-normalized UTC by the
   * BE. ``null`` ONLY when every source column is NULL (rare but
   * real): consumers must handle it explicitly
   * (``formatDeferHoldSince`` renders "unknown time").
   */
  since: string | null;
  kind: DeferHolderKind;
}

/** Response payload of ``GET /api/queues/defer-blocked``. */
export interface DeferBlockedStatus {
  defer_blocked: boolean;
  pending_count: number;
  holders: DeferBlockHolder[];
}

export type DeferBlockSeverity = 'amber' | 'info' | 'red';

/** Render-ready warning derived from a defer-blocked payload. */
export interface DeferBlockIndicator {
  severity: DeferBlockSeverity;
  tooltip: string;
}

/**
 * ``<date>`` slot for the amber tooltip — deterministic (no locale
 * dependency): ISO ``2026-09-04T15:33:24+00:00`` (BE wire truth:
 * +00:00-normalized UTC) renders as ``2026-09-04 15:33 UTC`` so the
 * zone is unambiguous. Non-ISO input degrades to a truncated
 * string; missing input — including the wire's ``since: null`` —
 * reads "unknown time".
 */
export function formatDeferHoldSince(since: string | null | undefined): string {
  if (!since) {
    return 'unknown time';
  }
  return `${since.replace('T', ' ').slice(0, 16)} UTC`;
}

/**
 * Derive the defer-block warning affordance from the payload.
 *
 * Render gate: ONLY when ``pending_count > 0`` — zero pending defer
 * jobs ⇒ ``null`` (no render, no reserved space).
 *
 * Severities (mutually exclusive by construction):
 * - ``amber``  — any holder kind ``"paused"``: the block is held by a
 *   paused instance; operator action (resume/terminate) unblocks.
 *   Names the FIRST paused holder in the array (documented
 *   assumption — the payload order is the contract's ordering).
 * - ``info``   — holders present, all live: deferral working as
 *   designed.
 * - ``red``    — pending defer jobs exist but ``holders`` is empty:
 *   possible stuck state (nothing holds the block, jobs not moving).
 */
export function deferBlockIndicator(
  status: DeferBlockedStatus | null | undefined
): DeferBlockIndicator | null {
  if (!status || !(status.pending_count > 0)) {
    return null;
  }

  const holders = status.holders ?? [];

  if (holders.length === 0) {
    const plural = status.pending_count === 1 ? '' : 's';
    return {
      severity: 'red',
      tooltip: `${status.pending_count} pending defer job${plural} with no holder — possibly stuck?`,
    };
  }

  const paused = holders.find((h) => h.kind === 'paused');
  if (paused) {
    return {
      severity: 'amber',
      tooltip:
        `held by paused instance ${paused.instance_id} ` +
        `since ${formatDeferHoldSince(paused.since)} — resume or terminate to unblock`,
    };
  }

  const n = holders.length;
  return {
    severity: 'info',
    tooltip: `held by ${n} live mission${n === 1 ? '' : 's'}`,
  };
}
