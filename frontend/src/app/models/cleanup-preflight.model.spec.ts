import {
  cleanupDeferNote,
  CLEANUP_TRUTH_SPLIT_COPY,
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
    expect(CLEANUP_TRUTH_SPLIT_COPY).toBe(
      'Every ACTIVE job is cancelled, together with its whole subtree. ' +
        'Only missions holding nothing but settled mirrors — no live work ' +
        '— are kept.'
    );
  });

  it('starts with "Every ACTIVE job is cancelled" (uppercase E, no leading whitespace)', () => {
    expect(CLEANUP_TRUTH_SPLIT_COPY.startsWith('Every ACTIVE job is cancelled')).toBe(true);
  });

  it('ends with the canonical closing fragment', () => {
    expect(CLEANUP_TRUTH_SPLIT_COPY.endsWith('— are kept.')).toBe(true);
  });
});
