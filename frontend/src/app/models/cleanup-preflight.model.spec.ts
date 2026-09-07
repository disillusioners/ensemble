import {
  cleanupDeferNote,
  CLEANUP_TRUTH_SPLIT_COPY,
  CLEANUP_TRUTH_SURVIVOR_NOTE,
} from './cleanup-preflight.model';

describe('cleanup-preflight.model — cleanupDeferNote', () => {
  it('recommends resume or terminate for a paused holder', () => {
    expect(cleanupDeferNote('paused')).toBe(
      'Paused holder — resume or terminate this holder to unblock.'
    );
  });

  it('recommends force-complete or foreground resend for a stalled holder', () => {
    expect(cleanupDeferNote('stalled')).toBe(
      'Stalled holder — force-complete it, or re-send its message in the foreground.'
    );
  });

  it('does not recommend a dead-control action for live or unknown holders', () => {
    expect(cleanupDeferNote('live')).toBeNull();
    expect(cleanupDeferNote(undefined)).toBeNull();
    expect(cleanupDeferNote(null)).toBeNull();
  });
});

describe('cleanup-preflight.model — CLEANUP_TRUTH_SPLIT_COPY', () => {
  /**
   * Unblock-round ITEM 5 (2026-09-06): pin the canonical truth-split
   * sentence VERBATIM. The dialog template
   * (``system-cleanup-confirm-dialog.component.ts``) must render this
   * const EXACTLY — drift between the const and the rendered HTML
   * is the failure mode this spec catches. NO Angular TestBed; plain
   * TS only.
   *
   * The same sentence lives in:
   *   * BE: ``daemon/routers/jobs_management.py:cleanup_preflight``
   *   * docs: ``docs/job-task-system.md`` §8.5
   *   * this constant (single FE source of truth).
   */
  it('contains the canonical truth-split sentence VERBATIM', () => {
    // Cap-exception micro-round (2026-09-06) — ITEM 2 tighten:
    // the truth-survivor caption was broadened from "no live work"
    // to "settled mirrors, or running Tasks without JobItems" so
    // the caption matches the filter's TRUE semantics (a
    // live-Task-only instance is a real survivor, NOT a
    // contradiction of the literal "no live work" reading).
    expect(CLEANUP_TRUTH_SPLIT_COPY).toBe(
      'Every ACTIVE job is cancelled, together with its whole subtree. ' +
        'Only missions holding settled mirrors, or running Tasks without ' +
        'JobItems — are kept.'
    );
  });

  it('starts with "Every ACTIVE job is cancelled" (uppercase E, no leading whitespace)', () => {
    expect(CLEANUP_TRUTH_SPLIT_COPY.startsWith('Every ACTIVE job is cancelled')).toBe(true);
  });

  it('ends with the canonical closing fragment', () => {
    expect(CLEANUP_TRUTH_SPLIT_COPY.endsWith('JobItems — are kept.')).toBe(true);
  });

  it('mentions both "settled mirrors" AND "running Tasks without JobItems"', () => {
    // Tightened wording — both survival shapes named explicitly so
    // the wording stays truthful to the truth-survivor filter
    // (a live-Task-only instance is a survivor; a settled-mirror-
    // only instance is a survivor; an active-mission-JobItem
    // holder is NOT).
    expect(CLEANUP_TRUTH_SPLIT_COPY).toContain('settled mirrors');
    expect(CLEANUP_TRUTH_SPLIT_COPY).toContain('running Tasks without JobItems');
  });
});

describe('cleanup-preflight.model — CLEANUP_TRUTH_SURVIVOR_NOTE', () => {
  /**
   * Unblock-round ITEM 11 (2026-09-06) — pin the truth-survivor
   * note. The dialog renders this when ``live_instance_ids.length >
   * 0`` (an explanatory fragment that clarifies the list is filtered
   * to the post-Bucket-2 survival subset). Drift breaks the suite.
   */
  it('is exported as a non-empty string', () => {
    expect(typeof CLEANUP_TRUTH_SURVIVOR_NOTE).toBe('string');
    expect(CLEANUP_TRUTH_SURVIVOR_NOTE.length).toBeGreaterThan(0);
  });

  it('mentions "truth-survivor" so a future reader knows the shape', () => {
    expect(CLEANUP_TRUTH_SURVIVOR_NOTE).toContain('Truth-survivor');
  });

  it('mentions "cancellable" so the Bucket-1/2 semantics are documented', () => {
    // Cap-exception micro-round fold (ITEM 5, 2026-09-06): the
    // survivor-note phrasing was broadened from "Bucket-2-cancellable"
    // to "cancellable active or queued (non-mirror) jobs" because
    // BOTH Bucket 1 (queued batch UPDATE) and Bucket 2 (active
    // per-row cancel) target the same set of rows this probe
    // excludes. The new wording is grep-stable and aligns with the
    // docstring on
    // ``SQLModelInstanceRepository.has_real_active_or_queued_work``.
    expect(CLEANUP_TRUTH_SURVIVOR_NOTE).toContain('cancellable');
  });
});

describe('cleanup-preflight.model — CleanupPreflight type', () => {
  /**
   * Unblock-round ITEM 12 (2026-09-06) — pin that ``defer_holder_kind``
   * is a TS-side annotation only, NOT a wire-payload field. The
   * preflight endpoint does NOT emit it; callers populate it from the
   * sibling ``/api/queues/defer-blocked`` endpoint. This spec asserts
   * the type surface, not the wire shape.
   *
   * Plain-TS only — no Angular TestBed.
   */
  it('exposes defer_holder_kind as an optional annotation', () => {
    const sample: CleanupPreflight = {
      bad_state_count: 0,
      zombie_instance_count: 0,
      // defer_holder_kind is optional + may be null
      defer_holder_kind: null,
    };
    // Type check: the field accepts DeferHolderKind | null | undefined.
    expect(sample.defer_holder_kind).toBeNull();
  });

  it('lets defer_holder_kind be omitted entirely (NOT a wire field)', () => {
    const sample: CleanupPreflight = {
      bad_state_count: 0,
      zombie_instance_count: 0,
      // defer_holder_kind deliberately omitted
    };
    expect(sample.defer_holder_kind).toBeUndefined();
  });
});
