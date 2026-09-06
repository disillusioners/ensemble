import {
  DeferBlockedStatus,
  DeferBlockHolder,
  deferBlockIndicator,
  formatDeferHoldSince,
} from './defer-blocked.model';

/**
 * Logic-mirror spec for the defer-blocked warning helper — the pure
 * severity/tooltip derivation behind the header badge's affordance.
 * Tooltips are asserted EXACTLY per severity (contract wording).
 */
describe('defer-blocked.model — deferBlockIndicator', () => {
  const holder = (overrides?: Partial<DeferBlockHolder>): DeferBlockHolder => ({
    instance_id: 'inst-123',
    agent: 'leader',
    status: 'processing',
    // BE wire truth: ISO-8601 +00:00-normalized UTC (NOT a trailing Z).
    since: '2026-09-04T15:33:24+00:00',
    kind: 'live',
    ...(overrides ?? {}),
  });

  const payload = (overrides?: Partial<DeferBlockedStatus>): DeferBlockedStatus => ({
    defer_blocked: true,
    pending_count: 2,
    holders: [],
    ...(overrides ?? {}),
  });

  // ── Render gate: pending_count === 0 ⇒ no render ────────────────────

  it('returns null for a null/undefined payload (defensive)', () => {
    expect(deferBlockIndicator(null)).toBeNull();
    expect(deferBlockIndicator(undefined)).toBeNull();
  });

  it('returns null when pending_count is 0 — no render, even with holders present', () => {
    expect(
      deferBlockIndicator(payload({ pending_count: 0, holders: [holder()] }))
    ).toBeNull();
  });

  it('returns null when pending_count is negative (defensive)', () => {
    expect(deferBlockIndicator(payload({ pending_count: -1 }))).toBeNull();
  });

  // ── RED anomaly: pending defer jobs with NO holder ──────────────────

  it('RED when pending_count > 0 and holders empty — exact tooltip (plural)', () => {
    const warn = deferBlockIndicator(payload({ pending_count: 3, holders: [] }));
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('red');
    expect(warn!.tooltip).toBe('3 pending defer jobs with no holder — possibly stuck?');
  });

  it('RED tooltip uses the singular when exactly one job is pending', () => {
    const warn = deferBlockIndicator(payload({ pending_count: 1, holders: [] }));
    expect(warn!.tooltip).toBe('1 pending defer job with no holder — possibly stuck?');
  });

  it('RED fires even when defer_blocked is false — pending_count > 0 + no holder is the anomaly', () => {
    const warn = deferBlockIndicator(
      payload({ defer_blocked: false, pending_count: 2, holders: [] })
    );
    expect(warn!.severity).toBe('red');
  });

  // ── AMBER: any paused holder ────────────────────────────────────────

  it('AMBER when any holder is paused — exact tooltip names instance and since (UTC-suffixed)', () => {
    const warn = deferBlockIndicator(
      payload({
        holders: [holder({ kind: 'paused', instance_id: 'abc-999', since: '2026-09-04T08:05:00+00:00' })],
      })
    );
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toBe(
      'held by paused instance abc-999 since 2026-09-04 08:05 UTC — resume or terminate to unblock'
    );
  });

  it('AMBER wins over live-only holders when the paused holder is NOT first', () => {
    const warn = deferBlockIndicator(
      payload({
        holders: [
          holder({ instance_id: 'live-1', kind: 'live' }),
          holder({ instance_id: 'paused-2', kind: 'paused' }),
          holder({ instance_id: 'live-3', kind: 'live' }),
        ],
      })
    );
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toContain('paused instance paused-2');
  });

  it('AMBER tooltip reads "unknown time" when the paused holder carries no since', () => {
    const warn = deferBlockIndicator(
      payload({ holders: [holder({ kind: 'paused', since: '' })] })
    );
    expect(warn!.tooltip).toBe(
      `held by paused instance inst-123 since unknown time — resume or terminate to unblock`
    );
  });

  it('AMBER tooltip reads "unknown time" when since is null — wire contract allows null (every source column NULL)', () => {
    // P0 type-truth: DeferBlockHolder.since is ``string | null`` on the
    // wire; the null path must be handled explicitly and render the
    // same degradation as the empty-string path ("unknown time", no
    // fabricated " UTC" suffix — null is not a timestamp).
    const warn = deferBlockIndicator(
      payload({ holders: [holder({ kind: 'paused', since: null })] })
    );
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toBe(
      'held by paused instance inst-123 since unknown time — resume or terminate to unblock'
    );
  });

  // ── AMBER: any stalled holder (WS2 mirrors-only kind) ────────────────

  it('AMBER when any holder is stalled — exact tooltip distinguishes from paused', () => {
    // WS2: a non-paused witness whose gate-busy state is EXCLUSIVELY
    // its own settled message mirrors (the WS1 carve-out test). The
    // tooltip copy is distinct from paused: "no live work; safe to
    // force-complete" instead of "resume or terminate to unblock".
    const warn = deferBlockIndicator(
      payload({
        holders: [holder({ kind: 'stalled', instance_id: 'stl-001', since: '2026-09-05T11:00:00+00:00' })],
      })
    );
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toBe(
      'held by stalled instance stl-001 since 2026-09-05 11:00 UTC — no live work; safe to force-complete'
    );
  });

  it('AMBER (paused) wins over AMBER (stalled) when both are present — paused tooltip', () => {
    // Paused always wins over stalled: a paused instance's actionable
    // unblock is always resume/terminate, never force-complete — so the
    // paused tooltip wording must surface, not the stalled one.
    const warn = deferBlockIndicator(
      payload({
        holders: [
          holder({ instance_id: 'stl-A', kind: 'stalled' }),
          holder({ instance_id: 'pau-B', kind: 'paused', since: '2026-09-05T08:00:00+00:00' }),
        ],
      })
    );
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toContain('paused instance pau-B');
    expect(warn!.tooltip).toContain('resume or terminate to unblock');
    expect(warn!.tooltip).not.toContain('safe to force-complete');
  });

  it('AMBER (stalled) wins over INFO when stalled is present but paused is not', () => {
    const warn = deferBlockIndicator(
      payload({
        holders: [
          holder({ instance_id: 'live-1', kind: 'live' }),
          holder({ instance_id: 'stl-2', kind: 'stalled', since: '2026-09-05T09:00:00+00:00' }),
        ],
      })
    );
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toContain('stalled instance stl-2');
    expect(warn!.tooltip).toContain('no live work; safe to force-complete');
  });

  it('AMBER (stalled) tooltip reads "unknown time" when the stalled holder carries no since', () => {
    const warn = deferBlockIndicator(
      payload({ holders: [holder({ kind: 'stalled', since: '' })] })
    );
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toBe(
      'held by stalled instance inst-123 since unknown time — no live work; safe to force-complete'
    );
  });

  it('AMBER (stalled) tooltip reads "unknown time" when since is null', () => {
    // Same null-tolerant handling as paused — null is not a timestamp,
    // no fabricated " UTC" suffix.
    const warn = deferBlockIndicator(
      payload({ holders: [holder({ kind: 'stalled', since: null })] })
    );
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toBe(
      'held by stalled instance inst-123 since unknown time — no live work; safe to force-complete'
    );
  });

  // ── INFO: holders present, all live ─────────────────────────────────

  it('INFO when holders are live-only — exact tooltip (singular)', () => {
    const warn = deferBlockIndicator(payload({ holders: [holder()] }));
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('info');
    expect(warn!.tooltip).toBe('held by 1 live mission');
  });

  it('INFO tooltip pluralizes with the holder count', () => {
    const warn = deferBlockIndicator(
      payload({ holders: [holder({ instance_id: 'a' }), holder({ instance_id: 'b' })] })
    );
    expect(warn!.tooltip).toBe('held by 2 live missions');
  });

  // ── formatDeferHoldSince ────────────────────────────────────────────

  describe('formatDeferHoldSince', () => {
    it('renders ISO input as locale-free "YYYY-MM-DD HH:MM UTC"', () => {
      // Wire truth: BE normalizes to +00:00 (not Z).
      expect(formatDeferHoldSince('2026-09-04T15:33:24+00:00')).toBe('2026-09-04 15:33 UTC');
      // Defensive parity: a trailing-Z variant truncates identically.
      expect(formatDeferHoldSince('2026-09-04T15:33:24Z')).toBe('2026-09-04 15:33 UTC');
    });

    it('returns "unknown time" for empty/null input — null is not a timestamp, no UTC suffix', () => {
      expect(formatDeferHoldSince('')).toBe('unknown time');
      expect(formatDeferHoldSince(null)).toBe('unknown time');
      expect(formatDeferHoldSince(undefined)).toBe('unknown time');
    });

    it('truncates non-ISO strings instead of throwing (degraded path keeps the zone suffix)', () => {
      expect(formatDeferHoldSince('not-a-date-but-long-enough')).toBe('not-a-date-but-l UTC');
    });
  });
});
