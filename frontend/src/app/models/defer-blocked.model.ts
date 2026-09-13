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
        `held by stalled mission ${stalled.instance_id} ` +
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

/**
 * Kind-rank for the page-banner holder ordering. The same paused >
 * stalled > live precedence as ``deferBlockIndicator`` (P4 task 2:
 * "paused > stalled > live order").
 *
 * The numeric ranks are a stable, spec-able contract — adding a new
 * kind later requires an explicit rank decision (the comparator is
 * exported below as ``compareDeferHolderKind``).
 */
export const DEFER_HOLDER_KIND_RANK: Readonly<Record<DeferHolderKind, number>> = {
  paused: 0,
  stalled: 1,
  live: 2,
};

/**
 * Sort-comparator for ``DeferHolderKind`` — the page banner's
 * ordered holder list keeps the wire's within-kind order (stable
 * sort) and only reorders across kinds.
 */
export function compareDeferHolderKind(
  a: DeferHolderKind,
  b: DeferHolderKind
): number {
  return DEFER_HOLDER_KIND_RANK[a] - DEFER_HOLDER_KIND_RANK[b];
}

/**
 * Page-banner holder ordering. Returns a NEW array (no mutation) so
 * callers can wire it straight into a signal without aliasing the
 * wire payload. Stable within each kind — the wire's per-kind order
 * is preserved (the existing helper ``deferBlockAction`` picks
 * ``Array.prototype.find`` so within-kind order already drives
 * "first paused wins"; this helper generalises that to the full
 * list).
 */
export function orderDeferHolders(
  holders: readonly DeferBlockHolder[]
): DeferBlockHolder[] {
  // ``Array.prototype.sort`` is stable in V8 (Chrome, Node) and all
  // modern browsers (since 2019); we do NOT need a custom tiebreak.
  return [...holders].sort((a, b) => compareDeferHolderKind(a.kind, b.kind));
}

/**
 * Render-ready banner state for the page-level defer-blocked banner
 * (P4 task 1).
 *
 * The existing ``deferBlockIndicator`` returns a tooltip-shaped
 * payload (severity + single sentence). The PAGE banner needs
 * separate title and body fields so the template can render a
 * heading + paragraph instead of a single-line tooltip. The two
 * helpers coexist — the indicator still drives the header badge
 * (no copy change there); this helper drives the new page banner.
 *
 * Render gate (plan task 1 verbatim): hidden ONLY when no data AND
 * no anomaly. Concretely:
 *   - ``null`` payload ⇒ return ``null`` (nothing to surface)
 *   - ``pending_count === 0`` AND holders empty ⇒ return ``null``
 *   - ``pending_count > 0`` AND holders empty ⇒ RED-anomaly (the
 *     "no holder" state IS the anomaly that justifies rendering)
 *   - ``pending_count > 0`` AND holders present ⇒ AMBER or INFO per
 *     the existing severity conjunction
 *
 * ``isAnomaly`` is exported as a convenience for the template's
 * anomaly-specific copy branch (the "Open System Cleanup" affordance
 * lives only in the anomaly path).
 */
export interface DeferPageBanner {
  severity: DeferBlockSeverity;
  /** Short heading for the banner — e.g. "Defer-blocked" or "Possibly stuck". */
  title: string;
  /** Body sentence(s) explaining the state — e.g. "5 messages held by paused instance …" */
  body: string;
  /** ``true`` iff the render gate fired for the anomaly (pending > 0, holders == []). */
  isAnomaly: boolean;
  /** The payload's holder list (already deferred — empty in the anomaly case). */
  holders: DeferBlockHolder[];
  /** The payload's pending count — exposes the raw number for the anomaly copy branch. */
  pendingCount: number;
}

/**
 * Singular / plural suffix helper for "message" / "messages" — the
 * defer lane is the ONLY place where the same noun carries singular
 * grammar rules, so we keep it local to the defer helpers (not a
 * global string util).
 */
function deferPendingNoun(count: number): string {
  return count === 1 ? 'message' : 'messages';
}

/**
 * Derive the page-banner state from the defer payload. The
 * severity-table truth is the SAME conjunction the indicator
 * helper uses; this helper just splits the single-line tooltip into
 * a title + body pair and tags the anomaly branch.
 */
export function deferPageBanner(
  status: DeferBlockedStatus | null | undefined
): DeferPageBanner | null {
  if (!status) {
    return null;
  }
  const pendingCount = status.pending_count;
  const holders = status.holders ?? [];

  // Render gate (matches ``deferBlockIndicator``): no pending defer
  // pressure ⇒ no banner — even if holders happen to be present.
  // The page banner's extra branch over the indicator is the
  // title/body SPLIT, not a new render gate.
  if (!(pendingCount > 0)) {
    return null;
  }

  // Anomaly: pending > 0 + zero holders.
  if (holders.length === 0) {
    const noun = deferPendingNoun(pendingCount);
    return {
      severity: 'red',
      title: 'Possibly stuck',
      body: `${pendingCount} ${noun} held with no holder — possibly stuck? Open System Cleanup to reconcile.`,
      isAnomaly: true,
      holders: [],
      pendingCount,
    };
  }

  // Holders present — severity per the existing conjunction.
  const ordered = orderDeferHolders(holders);

  const paused = ordered.find((h) => h.kind === 'paused');
  if (paused) {
    const noun = deferPendingNoun(pendingCount);
    return {
      severity: 'amber',
      title: 'Defer-blocked',
      body: `${pendingCount} ${noun} held by paused instance ${paused.instance_id} since ${formatDeferHoldSince(paused.since)} — resume or terminate to unblock.`,
      isAnomaly: false,
      holders: ordered,
      pendingCount,
    };
  }

  const stalled = ordered.find((h) => h.kind === 'stalled');
  if (stalled) {
    const noun = deferPendingNoun(pendingCount);
    return {
      severity: 'amber',
      title: 'Defer-blocked',
      body: `${pendingCount} ${noun} held by stalled mission ${stalled.instance_id} since ${formatDeferHoldSince(stalled.since)} — no live work; safe to force-complete.`,
      isAnomaly: false,
      holders: ordered,
      pendingCount,
    };
  }

  // All-live path — deferral working as designed, but the banner
  // stays visible (the message gate is non-zero).
  const noun = deferPendingNoun(pendingCount);
  return {
    severity: 'info',
    title: 'Defer-blocked',
    body: `${pendingCount} ${noun} held by ${holders.length} live mission${holders.length === 1 ? '' : 's'}.`,
    isAnomaly: false,
    holders: ordered,
    pendingCount,
  };
}
