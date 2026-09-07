import { DeferHolderKind } from './defer-blocked.model';

/**
 * Read-only payload returned by GET /api/jobs/cleanup/preflight.
 *
 * The first two counts are always present on the wire. The remaining
 * fields are optional so the UI remains compatible with older daemons
 * while the richer cleanup split rolls out.
 *
 * **NOTE (unblock-round ITEM 12, 2026-09-06):** ``defer_holder_kind``
 * is documented here ONLY for type-completeness — it is NOT on the
 * preflight wire (``GET /api/jobs/cleanup/preflight`` does NOT emit
 * it). The field is sourced from the SEPARATE
 * ``GET /api/queues/defer-blocked`` endpoint and POPULATED BY THE
 * CALLER (see ``frontend/src/app/pages/jobs/jobs.component.ts``
 * around line 558 where the preflight is composed with the
 * ``/api/queues/defer-blocked`` snapshot). The preflight payload
 * carries the count; the holder kind is FE-composed from a sibling
 * endpoint. Do NOT consume ``defer_holder_kind`` as a wire-payload
 * field — it is a TS-side annotation only.
 */
export interface CleanupPreflight {
  bad_state_count: number;
  zombie_instance_count: number;
  live_instance_count?: number;
  live_instance_ids?: string[];
  defer_blocked_count?: number;
  /** NOT on the preflight wire — populated by the caller. See the
   *  interface docstring (unblock-round ITEM 12, 2026-09-06). */
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
  'Only missions holding settled mirrors, or running Tasks without',
  'JobItems — are kept.',
].join(' ');

/**
 * Unblock-round ITEM 11 (2026-09-06, `fix/defer-self-witness-and-cleanup`)
 * — the `live_instance_ids` list is the truth-survivor set:
 * non-terminal ∧ not-zombie ∧ no non-mirror `active`/`queued`
 * JobItem. Every ID in the dialog represents an instance cleanup
 * will NOT terminate (a holder of a non-mirror ACTIVE mission
 * JobItem is NOT a truth-survivor despite being a non-zombie —
 * Bucket 2 cancels + cascades). When the list is non-empty, the
 * dialog surfaces this explanatory fragment alongside the IDs so
 * the operator knows the list is filtered to the post-Bucket-2
 * survival subset, not the round-2 over-promise shape.
 *
 * Pinned shape — the FE spec in `cleanup-preflight.model.spec.ts`
 * asserts this const is exported as ``CLEANUP_TRUTH_SURVIVOR_NOTE``
 * verbatim, so any drift between the const and a render-side
 * re-quote breaks the suite.
 */
export const CLEANUP_TRUTH_SURVIVOR_NOTE =
  'Truth-survivor listing: every ID below is a non-terminal ' +
  'instance cleanup will NOT terminate (no cancellable active or ' +
  'queued (non-mirror) jobs; live Tasks or non-terminal children may ' +
  'still anchor them).';

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
