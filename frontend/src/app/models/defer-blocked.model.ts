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
export type DeferHolderKind = 'paused' | 'live' | 'stalled';

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
 * - ``amber``  — any holder kind ``"stalled"`` (WS2): a non-paused
 *   witness held up EXCLUSIVELY by its own settled message mirrors
 *   (the WS1 carve-out test — no live task on the instance, no
 *   active/queued NON-defer job on that instance). Operator-actionable
 *   via force-complete of the holder's mirrors (WS4 will ship the
 *   cleanup mechanic). Names the FIRST stalled holder in the array.
 *   Paused always wins over stalled (paused is its own kind with
 *   its own distinct remediation — resume/terminate — so the
 *   operator-actionable status wins). Tooltip wording DISTINGUISHES
 *   the two amber kinds: paused = "resume or terminate to unblock";
 *   stalled = "no live work; safe to force-complete".
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

  const stalled = holders.find((h) => h.kind === 'stalled');
  if (stalled) {
    return {
      severity: 'amber',
      tooltip:
        `held by stalled instance ${stalled.instance_id} ` +
        `since ${formatDeferHoldSince(stalled.since)} — no live work; safe to force-complete`,
    };
  }

  const n = holders.length;
  return {
    severity: 'info',
    tooltip: `held by ${n} live mission${n === 1 ? '' : 's'}`,
  };
}

/** One operator action the defer warning can offer for a holder. */
export interface DeferBlockAction {
  /** The holder the actions target (paused > stalled precedence — same
   * ordering the indicator tooltip uses). */
  holder: DeferBlockHolder;
  /**
   * Force-complete is OFFERED only for the mirrors-only ``stalled``
   * kind (the SERVER re-verifies mirrors-only at execution time via
   * the canonical probe — the FE gate is a UX gate, not the safety
   * gate). Paused holders are unblocked by resume/terminate from the
   * instance surface, so the button is disabled for them.
   */
  forceCompleteAllowed: boolean;
}

/**
 * Derive the holder-targeted unstick actions from the payload
 * (WS4). Returns ``null`` when no action is offered: no payload, zero
 * pending defer jobs (same render gate as ``deferBlockIndicator``),
 * or no instance-backed actionable holder.
 *
 * Precedence matches the indicator: the first ``paused`` holder wins
 * (its remediation is resume/terminate, so force-complete is NOT
 * allowed), then the first ``stalled`` holder (force-complete IS
 * allowed). Live-only payloads offer no action — the deferral is
 * working as designed.
 */
export function deferBlockAction(
  status: DeferBlockedStatus | null | undefined
): DeferBlockAction | null {
  if (!status || !(status.pending_count > 0)) {
    return null;
  }

  const holders = status.holders ?? [];

  const paused = holders.find((h) => h.kind === 'paused');
  if (paused) {
    return { holder: paused, forceCompleteAllowed: false };
  }

  const stalled = holders.find((h) => h.kind === 'stalled');
  if (stalled) {
    return { holder: stalled, forceCompleteAllowed: true };
  }

  return null;
}
