import {
  DeferBlockedStatus,
  DeferBlockHolder,
  deferBlockIndicator,
  deferBlockAction,
  DeferBlockAction,
  deferPageBanner,
  orderDeferHolders,
  compareDeferHolderKind,
  DEFER_HOLDER_KIND_RANK,
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
      'held by stalled mission stl-001 since 2026-09-05 11:00 UTC — no live work; safe to force-complete'
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
    expect(warn!.tooltip).toContain('stalled mission stl-2');
    expect(warn!.tooltip).toContain('no live work; safe to force-complete');
  });

  it('AMBER (stalled) tooltip reads "unknown time" when the stalled holder carries no since', () => {
    const warn = deferBlockIndicator(
      payload({ holders: [holder({ kind: 'stalled', since: '' })] })
    );
    expect(warn).not.toBeNull();
    expect(warn!.severity).toBe('amber');
    expect(warn!.tooltip).toBe(
      'held by stalled mission inst-123 since unknown time — no live work; safe to force-complete'
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
      'held by stalled mission inst-123 since unknown time — no live work; safe to force-complete'
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

// ── WS4 holder actions — deferBlockAction ───────────────────────────────

describe('defer-blocked.model — deferBlockAction (WS4)', () => {
  const holder = (overrides?: Partial<DeferBlockHolder>): DeferBlockHolder => ({
    instance_id: 'inst-123',
    agent: 'leader',
    status: 'processing',
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

  it('returns null for a null/undefined payload (defensive)', () => {
    expect(deferBlockAction(null)).toBeNull();
    expect(deferBlockAction(undefined)).toBeNull();
  });

  it('returns null when pending_count is 0 — same render gate as the indicator', () => {
    expect(
      deferBlockAction(
        payload({ pending_count: 0, holders: [holder({ kind: 'stalled' })] })
      )
    ).toBeNull();
  });

  it('returns null when holders are live-only — deferral working as designed', () => {
    expect(
      deferBlockAction(payload({ holders: [holder({ kind: 'live' })] }))
    ).toBeNull();
  });

  it('returns null when holders list is empty (RED anomaly has no target)', () => {
    expect(deferBlockAction(payload({ holders: [] }))).toBeNull();
  });

  it('stalled holder → action with forceCompleteAllowed=true', () => {
    const action: DeferBlockAction | null = deferBlockAction(
      payload({ holders: [holder({ instance_id: 'inst-stall', kind: 'stalled' })] })
    );
    expect(action).not.toBeNull();
    expect(action!.holder.instance_id).toBe('inst-stall');
    expect(action!.holder.kind).toBe('stalled');
    expect(action!.forceCompleteAllowed).toBe(true);
  });

  it('paused holder → action with forceCompleteAllowed=false (resume/terminate is the remediation)', () => {
    const action = deferBlockAction(
      payload({ holders: [holder({ instance_id: 'inst-pause', kind: 'paused' })] })
    );
    expect(action).not.toBeNull();
    expect(action!.holder.instance_id).toBe('inst-pause');
    expect(action!.forceCompleteAllowed).toBe(false);
  });

  it('paused wins over stalled — same precedence as the indicator', () => {
    const action = deferBlockAction(
      payload({
        holders: [
          holder({ instance_id: 'inst-stall', kind: 'stalled' }),
          holder({ instance_id: 'inst-pause', kind: 'paused' }),
        ],
      })
    );
    expect(action!.holder.instance_id).toBe('inst-pause');
    expect(action!.forceCompleteAllowed).toBe(false);
  });

  it('names the FIRST stalled holder when several exist', () => {
    const action = deferBlockAction(
      payload({
        holders: [
          holder({ instance_id: 'inst-a', kind: 'stalled' }),
          holder({ instance_id: 'inst-b', kind: 'stalled' }),
        ],
      })
    );
    expect(action!.holder.instance_id).toBe('inst-a');
  });
});

// ── P4 page-banner helper (vs. the legacy tooltip helper) ──────────────

describe('defer-blocked.model — orderDeferHolders (P4 task 2)', () => {
  const holder = (overrides?: Partial<DeferBlockHolder>): DeferBlockHolder => ({
    instance_id: 'inst-123',
    agent: 'leader',
    status: 'processing',
    since: '2026-09-04T15:33:24+00:00',
    kind: 'live',
    ...(overrides ?? {}),
  });

  it('returns a NEW array (does not mutate the input)', () => {
    const input: DeferBlockHolder[] = [
      holder({ instance_id: 'live-A', kind: 'live' }),
      holder({ instance_id: 'pau-B', kind: 'paused' }),
    ];
    const copy = orderDeferHolders(input);
    expect(copy).not.toBe(input);
    expect(input.map((h) => h.instance_id)).toEqual(['live-A', 'pau-B']);
  });

  it('places paused holders FIRST, then stalled, then live', () => {
    const out = orderDeferHolders([
      holder({ instance_id: 'live-1', kind: 'live' }),
      holder({ instance_id: 'pau-2', kind: 'paused' }),
      holder({ instance_id: 'stl-3', kind: 'stalled' }),
      holder({ instance_id: 'live-4', kind: 'live' }),
    ]);
    expect(out.map((h) => h.instance_id)).toEqual([
      'pau-2',
      'stl-3',
      'live-1',
      'live-4',
    ]);
  });

  it('preserves the wire order WITHIN each kind (stable sort)', () => {
    const out = orderDeferHolders([
      holder({ instance_id: 'live-1', kind: 'live' }),
      holder({ instance_id: 'pau-2', kind: 'paused' }),
      holder({ instance_id: 'pau-3', kind: 'paused' }),
      holder({ instance_id: 'stl-4', kind: 'stalled' }),
      holder({ instance_id: 'live-5', kind: 'live' }),
    ]);
    expect(out.map((h) => h.instance_id)).toEqual([
      'pau-2',
      'pau-3',
      'stl-4',
      'live-1',
      'live-5',
    ]);
  });

  it('handles an empty list — returns []', () => {
    expect(orderDeferHolders([])).toEqual([]);
  });

  it('handles a single-holder list', () => {
    const out = orderDeferHolders([holder({ instance_id: 'only', kind: 'stalled' })]);
    expect(out.map((h) => h.instance_id)).toEqual(['only']);
  });
});

describe('defer-blocked.model — compareDeferHolderKind (rank contract)', () => {
  it('ranks paused < stalled < live', () => {
    expect(DEFER_HOLDER_KIND_RANK.paused).toBeLessThan(DEFER_HOLDER_KIND_RANK.stalled);
    expect(DEFER_HOLDER_KIND_RANK.stalled).toBeLessThan(DEFER_HOLDER_KIND_RANK.live);
  });

  it('compareDeferHolderKind returns the rank difference', () => {
    expect(compareDeferHolderKind('paused', 'paused')).toBe(0);
    expect(compareDeferHolderKind('paused', 'stalled')).toBeLessThan(0);
    expect(compareDeferHolderKind('stalled', 'paused')).toBeGreaterThan(0);
    expect(compareDeferHolderKind('live', 'paused')).toBeGreaterThan(0);
  });
});

describe('defer-blocked.model — deferPageBanner (P4 task 1)', () => {
  const holder = (overrides?: Partial<DeferBlockHolder>): DeferBlockHolder => ({
    instance_id: 'inst-123',
    agent: 'leader',
    status: 'processing',
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

  // ── Render gate ─────────────────────────────────────────────────────

  it('returns null for a null/undefined payload (defensive)', () => {
    expect(deferPageBanner(null)).toBeNull();
    expect(deferPageBanner(undefined)).toBeNull();
  });

  it('hidden when pending_count = 0 AND holders empty — no data, no anomaly', () => {
    // Plan task 1: "hidden only when no data AND no anomaly"
    expect(
      deferPageBanner(payload({ pending_count: 0, holders: [] }))
    ).toBeNull();
  });

  it('hidden when pending_count = 0 even with holders present — matches the indicator render gate', () => {
    // P4 task 1: the page banner uses the existing conjunction
    // (the indicator's gate is the source of truth — ``pending_count
    // === 0`` ⇒ null). The page banner inherits that gate so a
    // zero-pressure state never reserves space. The page banner's
    // extra branch is the ANOMALY path (``pending > 0 + holders
    // empty``), which the indicator helper already covers (red).
    expect(
      deferPageBanner(payload({ pending_count: 0, holders: [holder()] }))
    ).toBeNull();
  });

  // ── RED anomaly branch ──────────────────────────────────────────────

  it('RED anomaly: pending > 0 + holders empty ⇒ isAnomaly true', () => {
    const banner = deferPageBanner(payload({ pending_count: 3, holders: [] }));
    expect(banner).not.toBeNull();
    expect(banner!.severity).toBe('red');
    expect(banner!.isAnomaly).toBe(true);
    expect(banner!.title).toBe('Possibly stuck');
    expect(banner!.body).toContain('3 messages');
    expect(banner!.pendingCount).toBe(3);
    expect(banner!.holders).toEqual([]);
  });

  it('RED anomaly pluralizes pending_count (singular vs plural)', () => {
    const single = deferPageBanner(payload({ pending_count: 1, holders: [] }));
    expect(single!.body).toContain('1 message ');
    expect(single!.body).not.toContain('messages');
    const multi = deferPageBanner(payload({ pending_count: 2, holders: [] }));
    expect(multi!.body).toContain('2 messages');
  });

  // ── AMBER (paused) ──────────────────────────────────────────────────

  it('AMBER when any holder is paused — banner carries body + ordered holders', () => {
    const banner = deferPageBanner(
      payload({
        holders: [
          holder({ kind: 'paused', instance_id: 'pau-A', since: '2026-09-04T08:05:00+00:00' }),
        ],
      })
    );
    expect(banner).not.toBeNull();
    expect(banner!.severity).toBe('amber');
    expect(banner!.isAnomaly).toBe(false);
    expect(banner!.title).toBe('Defer-blocked');
    expect(banner!.body).toContain('paused instance pau-A');
    expect(banner!.body).toContain('resume or terminate to unblock');
    expect(banner!.holders.map((h) => h.instance_id)).toEqual(['pau-A']);
  });

  // ── AMBER (stalled) ─────────────────────────────────────────────────

  it('AMBER when any holder is stalled — distinct copy from paused', () => {
    const banner = deferPageBanner(
      payload({
        holders: [
          holder({ kind: 'stalled', instance_id: 'stl-A', since: '2026-09-05T11:00:00+00:00' }),
        ],
      })
    );
    expect(banner!.severity).toBe('amber');
    expect(banner!.body).toContain('stalled mission stl-A');
    expect(banner!.body).toContain('safe to force-complete');
  });

  // ── INFO (all-live) ─────────────────────────────────────────────────

  it('INFO when holders present and all live — deferral working as designed', () => {
    const banner = deferPageBanner(
      payload({
        holders: [
          holder({ instance_id: 'live-1' }),
          holder({ instance_id: 'live-2' }),
        ],
      })
    );
    expect(banner!.severity).toBe('info');
    expect(banner!.isAnomaly).toBe(false);
    expect(banner!.body).toContain('2 live missions');
  });

  // ── Holder ordering propagates into the banner ──────────────────────

  it('banner.holders is orderDeferHolders-shaped — paused first, then stalled, then live', () => {
    const banner = deferPageBanner(
      payload({
        holders: [
          holder({ instance_id: 'live-1', kind: 'live' }),
          holder({ instance_id: 'pau-2', kind: 'paused' }),
          holder({ instance_id: 'stl-3', kind: 'stalled' }),
        ],
      })
    );
    expect(banner!.holders.map((h) => h.instance_id)).toEqual([
      'pau-2',
      'stl-3',
      'live-1',
    ]);
  });

  // ── Cross-seam invariant: banner severity ↔ indicator severity ──────

  it('banner severity matches deferBlockIndicator severity for the same payload (page-level parity)', () => {
    // Plan test strategy: "banner severity identical whether data
    // arrives via poll tick or manual refresh" — equivalent here to
    // the page-level helper agreeing with the header indicator helper
    // (both read the same payload).
    const samples: DeferBlockedStatus[] = [
      payload({ pending_count: 0, holders: [] }), // null on both
      payload({ pending_count: 3, holders: [] }), // red on both
      payload({
        holders: [
          holder({ instance_id: 'pau-A', kind: 'paused' }),
          holder({ instance_id: 'live-1', kind: 'live' }),
        ],
      }), // amber on both
      payload({
        holders: [holder({ instance_id: 'stl-X', kind: 'stalled' })],
      }), // amber on both
      payload({ holders: [holder({ kind: 'live' })] }), // info on both
    ];
    for (const sample of samples) {
      const ind = deferBlockIndicator(sample);
      const bn = deferPageBanner(sample);
      // When the banner is hidden (no data + no anomaly), the indicator
      // is also null — invariant holds.
      if (bn === null) {
        expect(ind).toBeNull();
      } else {
        expect(bn.severity).toBe(ind!.severity);
      }
    }
  });
});
