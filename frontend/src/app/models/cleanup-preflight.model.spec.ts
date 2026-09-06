import { cleanupDeferNote } from './cleanup-preflight.model';

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
