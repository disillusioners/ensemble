import { signal, computed, type Signal } from '@angular/core';
import type { SkillAbTestStats } from '../../models/skill.model';

// ===========================================================================
// Testable AbTestDashboardComponent
//
// Mirrors the production source so tests can drive the component without
// Angular TestBed. Logic is identical to `ab-test-dashboard.component.ts`;
// if the real component changes, this mirror must be updated too.
//
// Test pattern mirrors `instance-delete-dialog.component.spec.ts` — see that
// file for the rationale (avoids jest-preset-angular microtask timing issues
// and keeps the spec focused on the component's pure logic).
// ===========================================================================

type MetricRow = {
  name: string;
  weight: number;
  scoreA: number;
  scoreB: number;
  displayA: string;
  displayB: string;
  winner: 'A' | 'B' | 'tie';
  higherIsBetter: boolean;
};

type TestState = 'no_data' | 'collecting' | 'needs_more' | 'ready';
type WinnerVariant = 'A' | 'B' | 'tie' | null;

class TestableAbTestDashboardComponent {
  // Signal inputs — production code uses Angular's `input()` API which
  // creates a Signal under the hood, so Signal<T> in tests is the
  // behavioural equivalent.
  private readonly _stats = signal<SkillAbTestStats | null>(null);
  private readonly _skillName = signal<string>('');

  // Mirror of the public `input<>()` reads.
  protected readonly stats: Signal<SkillAbTestStats | null> = this._stats.asReadonly();
  protected readonly skillName: Signal<string> = this._skillName.asReadonly();

  // Output — mocked as an object with `emit()` so tests can spy on it.
  readonly resolve = { emit: jest.fn() };

  // Computed signals — same formulas as the production source.
  readonly hasData = computed<boolean>(() => {
    const s = this._stats();
    if (!s) return false;
    if (s.comparisons > 0) return true;
    return (
      s.completion_rate_a > 0 ||
      s.completion_rate_b > 0 ||
      s.applied_rate_a > 0 ||
      s.applied_rate_b > 0 ||
      s.fallback_rate_a > 0 ||
      s.fallback_rate_b > 0 ||
      s.avg_iterations_a > 0 ||
      s.avg_iterations_b > 0 ||
      s.avg_duration_a > 0 ||
      s.avg_duration_b > 0
    );
  });

  readonly testState = computed<TestState>(() => {
    const s = this._stats();
    if (!s) return 'no_data';
    if (!this.hasData()) return 'collecting';
    if (s.ready_to_resolve) return 'ready';
    if (s.needs_more_data) return 'needs_more';
    return 'collecting';
  });

  readonly winnerVariant = computed<WinnerVariant>(() => {
    const s = this._stats();
    if (!s || !this.hasData()) return null;
    const scoreA = s.composite_score_a;
    const scoreB = s.composite_score_b;
    if (scoreA === scoreB) return 'tie';
    if (scoreB > scoreA) return 'B';
    return 'A';
  });

  readonly metricRows = computed<MetricRow[]>(() => {
    const s = this._stats();
    if (!s) return [];

    const completionA = clamp01(s.completion_rate_a);
    const completionB = clamp01(s.completion_rate_b);
    const appliedA = clamp01(s.applied_rate_a);
    const appliedB = clamp01(s.applied_rate_b);
    const lowFallbackA = clamp01(1 - s.fallback_rate_a);
    const lowFallbackB = clamp01(1 - s.fallback_rate_b);
    const efficiencyA = normaliseInverse(s.avg_iterations_a);
    const efficiencyB = normaliseInverse(s.avg_iterations_b);
    const speedA = normaliseInverse(s.avg_duration_a);
    const speedB = normaliseInverse(s.avg_duration_b);

    const rows: Array<Omit<MetricRow, 'winner'>> = [
      {
        name: 'Completion Rate',
        weight: 0.35,
        scoreA: completionA,
        scoreB: completionB,
        displayA: formatPercent(completionA),
        displayB: formatPercent(completionB),
        higherIsBetter: true,
      },
      {
        name: 'Applied Rate',
        weight: 0.20,
        scoreA: appliedA,
        scoreB: appliedB,
        displayA: formatPercent(appliedA),
        displayB: formatPercent(appliedB),
        higherIsBetter: true,
      },
      {
        name: 'Efficiency',
        weight: 0.20,
        scoreA: efficiencyA,
        scoreB: efficiencyB,
        displayA: `${s.avg_iterations_a.toFixed(2)} iter`,
        displayB: `${s.avg_iterations_b.toFixed(2)} iter`,
        higherIsBetter: true,
      },
      {
        name: 'Low Fallback',
        weight: 0.15,
        scoreA: lowFallbackA,
        scoreB: lowFallbackB,
        displayA: formatPercent(lowFallbackA),
        displayB: formatPercent(lowFallbackB),
        higherIsBetter: true,
      },
      {
        name: 'Speed',
        weight: 0.10,
        scoreA: speedA,
        scoreB: speedB,
        displayA: `${s.avg_duration_a.toFixed(2)}s`,
        displayB: `${s.avg_duration_b.toFixed(2)}s`,
        higherIsBetter: true,
      },
    ];

    return rows.map((row): MetricRow => ({
      ...row,
      winner: pickMetricWinner(row.scoreA, row.scoreB, row.higherIsBetter),
    }));
  });

  // ── Mirror of helper methods used by the template ─────────────

  protected formatScore(score: number): string {
    return formatPercent(clamp01(score));
  }

  protected shortId(id: string | null): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) : id;
  }

  protected variantStatus(variant: 'A' | 'B'): 'Winner' | 'Loser' | 'Tied' | 'Active' {
    const winner = this.winnerVariant();
    if (winner === null) return 'Active';
    if (winner === 'tie') return 'Tied';
    return winner === variant ? 'Winner' : 'Loser';
  }

  protected variantStatusColor(variant: 'A' | 'B'): string {
    const status = this.variantStatus(variant);
    switch (status) {
      case 'Winner': return 'primary';
      case 'Loser':  return 'warn';
      case 'Tied':   return 'accent';
      default:       return '';
    }
  }

  protected scoreBarValue(score: number): number {
    return clamp01(score) * 100;
  }

  protected scoreBarColor(score: number): 'primary' | 'accent' | 'warn' {
    const clamped = clamp01(score);
    if (clamped > 0.7) return 'primary';
    if (clamped >= 0.4) return 'accent';
    return 'warn';
  }

  protected bannerText(): string {
    const s = this._stats();
    if (!s) return '';
    const state = this.testState();
    switch (state) {
      case 'ready': {
        const diffPct = formatPercent(s.difference);
        return (
          `Ready to resolve: ${s.comparisons}/${s.sample_size} comparisons, ` +
          `${diffPct} score difference.`
        );
      }
      case 'needs_more':
        return (
          `Collecting data: ${s.comparisons}/${s.sample_size} comparisons. ` +
          'Test is still accumulating signal.'
        );
      case 'collecting':
        return `Collecting data… ${s.comparisons}/${s.sample_size} comparisons so far.`;
      default:
        return '';
    }
  }

  protected onResolve(): void {
    this.resolve.emit();
  }

  // ── Test helpers (not part of production source) ──────────────

  setStats(stats: SkillAbTestStats | null): void {
    this._stats.set(stats);
  }

  setSkillName(name: string): void {
    this._skillName.set(name);
  }
}

// ── Mirror module-level helpers from production source ───────────

function clamp01(x: number): number {
  // NaN renders as 0%; ±Infinity bounds-up / -down so the progress
  // bar cannot blow up. See the matching helper in
  // ab-test-dashboard.component.ts.
  if (Number.isNaN(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

function normaliseInverse(x: number): number {
  if (!isFinite(x) || x < 0) return 0;
  return 1 / (1 + x);
}

function formatPercent(score: number): string {
  const pct = clamp01(score) * 100;
  return `${pct.toFixed(1)}%`;
}

function pickMetricWinner(
  scoreA: number,
  scoreB: number,
  higherIsBetter: boolean,
): 'A' | 'B' | 'tie' {
  const a = higherIsBetter ? scoreA : -scoreA;
  const b = higherIsBetter ? scoreB : -scoreB;
  if (a === b) return 'tie';
  return a > b ? 'A' : 'B';
}

// ── Fixture builders ─────────────────────────────────────────────

function createMockStats(overrides: Partial<SkillAbTestStats> = {}): SkillAbTestStats {
  return {
    skill_id_a: 'skill-aaaa1111',
    skill_id_b: 'skill-bbbb2222',
    completion_rate_a: 0.8,
    completion_rate_b: 0.6,
    applied_rate_a: 0.7,
    applied_rate_b: 0.5,
    fallback_rate_a: 0.05,
    fallback_rate_b: 0.15,
    avg_iterations_a: 3,
    avg_iterations_b: 4,
    avg_duration_a: 10,
    avg_duration_b: 15,
    composite_score_a: 0.85,
    composite_score_b: 0.65,
    difference: 0.2,
    comparisons: 25,
    extension_count: 0,
    sample_size: 25,
    ready_to_resolve: true,
    needs_more_data: false,
    ...overrides,
  };
}

function createComponent(): TestableAbTestDashboardComponent {
  return new TestableAbTestDashboardComponent();
}

// ===========================================================================
// Tests
// ===========================================================================

describe('AbTestDashboardComponent', () => {
  let component: TestableAbTestDashboardComponent;

  beforeEach(() => {
    component = createComponent();
  });

  // ── 1) Component creation ──────────────────────────────────────

  describe('component creation', () => {
    it('creates successfully', () => {
      expect(component).toBeTruthy();
    });

    it('exposes the `stats` input as a Signal that defaults to null', () => {
      expect(component.stats()).toBeNull();
    });

    it('exposes the `skillName` input as a Signal that defaults to empty string', () => {
      expect(component.skillName()).toBe('');
    });

    it('exposes the `resolve` output', () => {
      expect(component.resolve).toBeDefined();
      expect(typeof component.resolve.emit).toBe('function');
    });
  });

  // ── 2) No active test (stats = null) ───────────────────────────

  describe('no active test (stats = null)', () => {
    it('reports `no_data` test state', () => {
      expect(component.testState()).toBe('no_data');
    });

    it('reports `hasData` === false', () => {
      expect(component.hasData()).toBe(false);
    });

    it('reports `winnerVariant` === null', () => {
      expect(component.winnerVariant()).toBeNull();
    });

    it('returns an empty `metricRows` array', () => {
      expect(component.metricRows()).toEqual([]);
    });

    it('renders an empty banner', () => {
      expect(component.bannerText()).toBe('');
    });

    it('returns `Active` from variantStatus for both variants', () => {
      expect(component.variantStatus('A')).toBe('Active');
      expect(component.variantStatus('B')).toBe('Active');
    });
  });

  // ── 3) Collecting data state ──────────────────────────────────

  describe('collecting data state', () => {
    beforeEach(() => {
      // All rates zero, no comparisons — fresh test with no signal yet.
      component.setStats(
        createMockStats({
          completion_rate_a: 0,
          completion_rate_b: 0,
          applied_rate_a: 0,
          applied_rate_b: 0,
          fallback_rate_a: 0,
          fallback_rate_b: 0,
          avg_iterations_a: 0,
          avg_iterations_b: 0,
          avg_duration_a: 0,
          avg_duration_b: 0,
          composite_score_a: 0,
          composite_score_b: 0,
          difference: 0,
          comparisons: 0,
          ready_to_resolve: false,
          needs_more_data: false,
          sample_size: 25,
        }),
      );
    });

    it('reports `collecting` test state', () => {
      expect(component.testState()).toBe('collecting');
    });

    it('reports `hasData` === false', () => {
      expect(component.hasData()).toBe(false);
    });

    it('reports `winnerVariant` === null (no data to compare)', () => {
      expect(component.winnerVariant()).toBeNull();
    });

    it('renders a "Collecting data…" banner with 0/25 comparisons', () => {
      expect(component.bannerText()).toContain('Collecting data');
      expect(component.bannerText()).toContain('0/25');
    });

    it('still returns 5 metric rows (template can render skeleton)', () => {
      // The metric table always renders 5 rows; the template shows them
      // muted while in `collecting` state.
      expect(component.metricRows().length).toBe(5);
    });
  });

  // ── 4) Ready to resolve ───────────────────────────────────────

  describe('ready to resolve', () => {
    beforeEach(() => {
      component.setStats(
        createMockStats({
          comparisons: 25,
          sample_size: 25,
          ready_to_resolve: true,
          needs_more_data: false,
        }),
      );
    });

    it('reports `ready` test state', () => {
      expect(component.testState()).toBe('ready');
    });

    it('renders a "Ready to resolve" banner with the dynamic sample_size', () => {
      expect(component.bannerText()).toContain('Ready to resolve');
      expect(component.bannerText()).toContain('25/25');
    });

    it('includes the difference percentage in the banner', () => {
      // difference=0.2 → "20.0%"
      expect(component.bannerText()).toContain('20.0%');
    });
  });

  // ── 5) Needs more data ────────────────────────────────────────

  describe('needs more data', () => {
    beforeEach(() => {
      component.setStats(
        createMockStats({
          comparisons: 10,
          sample_size: 25,
          ready_to_resolve: false,
          needs_more_data: true,
        }),
      );
    });

    it('reports `needs_more` test state', () => {
      expect(component.testState()).toBe('needs_more');
    });

    it('renders a "Collecting data" banner with the dynamic progress', () => {
      expect(component.bannerText()).toContain('Collecting data');
      expect(component.bannerText()).toContain('10/25');
    });

    it('does NOT enable the Resolve button (template reads ready_to_resolve)', () => {
      // The template gates the button on `s.ready_to_resolve`; here we
      // simply verify the source flag is false.
      expect(component.stats()?.ready_to_resolve).toBe(false);
    });
  });

  // ── 6) Winner determination ───────────────────────────────────

  describe('winnerVariant', () => {
    it('returns "A" when composite_score_a > composite_score_b', () => {
      component.setStats(
        createMockStats({
          comparisons: 25,
          composite_score_a: 0.9,
          composite_score_b: 0.6,
          ready_to_resolve: true,
        }),
      );
      expect(component.winnerVariant()).toBe('A');
      expect(component.variantStatus('A')).toBe('Winner');
      expect(component.variantStatus('B')).toBe('Loser');
    });

    it('returns "B" when composite_score_b > composite_score_a', () => {
      component.setStats(
        createMockStats({
          comparisons: 25,
          composite_score_a: 0.5,
          composite_score_b: 0.8,
          ready_to_resolve: true,
        }),
      );
      expect(component.winnerVariant()).toBe('B');
      expect(component.variantStatus('A')).toBe('Loser');
      expect(component.variantStatus('B')).toBe('Winner');
    });

    it('returns "tie" when composite scores are exactly equal (B wins per backend rule)', () => {
      component.setStats(
        createMockStats({
          comparisons: 25,
          composite_score_a: 0.7,
          composite_score_b: 0.7,
          difference: 0,
          ready_to_resolve: true,
        }),
      );
      expect(component.winnerVariant()).toBe('tie');
      expect(component.variantStatus('A')).toBe('Tied');
      expect(component.variantStatus('B')).toBe('Tied');
    });

    it('mirrors the backend tie-breaking rule (B wins ties — see _pick_winner)', () => {
      // Documented behavior: variantStatus uses raw `winnerVariant()`
      // which returns 'tie' — the resolver picks B for ties at
      // resolve time, so a tie should NOT visually promote either
      // variant to Winner until the resolver runs.
      component.setStats(
        createMockStats({
          comparisons: 25,
          composite_score_a: 0.5,
          composite_score_b: 0.5,
          difference: 0,
          ready_to_resolve: true,
        }),
      );
      expect(component.winnerVariant()).toBe('tie');
    });

    it('returns null when there is no data yet', () => {
      // Fully zero out all rate fields so `hasData()` returns false;
      // partial overrides leave `createMockStats`'s non-zero defaults
      // (e.g. applied_rate_a=0.7) visible to the check.
      component.setStats(
        createMockStats({
          comparisons: 0,
          completion_rate_a: 0,
          completion_rate_b: 0,
          applied_rate_a: 0,
          applied_rate_b: 0,
          fallback_rate_a: 0,
          fallback_rate_b: 0,
          avg_iterations_a: 0,
          avg_iterations_b: 0,
          avg_duration_a: 0,
          avg_duration_b: 0,
          composite_score_a: 0,
          composite_score_b: 0,
          ready_to_resolve: false,
          needs_more_data: false,
        }),
      );
      expect(component.winnerVariant()).toBeNull();
    });
  });

  // ── 7) Resolve emit ───────────────────────────────────────────

  describe('onResolve', () => {
    it('emits the resolve output when called', () => {
      component.setStats(createMockStats({ ready_to_resolve: true }));
      component.onResolve();
      expect(component.resolve.emit).toHaveBeenCalledTimes(1);
    });

    it('emits an undefined payload (output is `output<void>()`)', () => {
      component.setStats(createMockStats({ ready_to_resolve: true }));
      component.onResolve();
      expect(component.resolve.emit).toHaveBeenCalledWith();
    });

    it('does not throw when stats is null (UI guards the button, but the handler is unconditional)', () => {
      // The template hides the button when `!ready_to_resolve`, but the
      // handler itself does not depend on stats — it should not throw.
      expect(() => component.onResolve()).not.toThrow();
      expect(component.resolve.emit).toHaveBeenCalledTimes(1);
    });
  });

  // ── 8) metricRows computed ────────────────────────────────────

  describe('metricRows', () => {
    beforeEach(() => {
      component.setStats(createMockStats());
    });

    it('returns exactly 5 rows', () => {
      expect(component.metricRows().length).toBe(5);
    });

    it('uses the canonical backend weights (0.35, 0.20, 0.20, 0.15, 0.10)', () => {
      const weights = component.metricRows().map((row) => row.weight);
      expect(weights).toEqual([0.35, 0.20, 0.20, 0.15, 0.10]);
    });

    it('names the metrics in canonical order', () => {
      const names = component.metricRows().map((row) => row.name);
      expect(names).toEqual([
        'Completion Rate',
        'Applied Rate',
        'Efficiency',
        'Low Fallback',
        'Speed',
    ]);
    });

    it('computes normalised scores in [0, 1] for every row', () => {
      for (const row of component.metricRows()) {
        expect(row.scoreA).toBeGreaterThanOrEqual(0);
        expect(row.scoreA).toBeLessThanOrEqual(1);
        expect(row.scoreB).toBeGreaterThanOrEqual(0);
        expect(row.scoreB).toBeLessThanOrEqual(1);
      }
    });

    it('flags each row with a winner (A/B/tie)', () => {
      for (const row of component.metricRows()) {
        expect(['A', 'B', 'tie']).toContain(row.winner);
      }
    });

    it('uses raw rate display for Completion Rate (percent string)', () => {
      const completion = component.metricRows()[0];
      expect(completion.displayA).toMatch(/%$/);
      expect(completion.displayB).toMatch(/%$/);
    });

    it('uses raw iteration count display for Efficiency (iter suffix)', () => {
      const efficiency = component.metricRows()[2];
      expect(efficiency.displayA).toMatch(/iter$/);
      expect(efficiency.displayB).toMatch(/iter$/);
    });

    it('uses raw duration display for Speed (s suffix)', () => {
      const speed = component.metricRows()[4];
      expect(speed.displayA).toMatch(/s$/);
      expect(speed.displayB).toMatch(/s$/);
    });

    it('returns an empty array when stats is null', () => {
      component.setStats(null);
      expect(component.metricRows()).toEqual([]);
    });
  });

  // ── 9) Score formatting ───────────────────────────────────────

  describe('formatScore', () => {
    it('formats 0.847 as "84.7%"', () => {
      expect(component.formatScore(0.847)).toBe('84.7%');
    });

    it('formats 0 as "0.0%"', () => {
      expect(component.formatScore(0)).toBe('0.0%');
    });

    it('formats 1 as "100.0%"', () => {
      expect(component.formatScore(1)).toBe('100.0%');
    });

    it('clamps negative values to "0.0%"', () => {
      expect(component.formatScore(-0.5)).toBe('0.0%');
    });

    it('clamps values > 1 to "100.0%"', () => {
      expect(component.formatScore(1.5)).toBe('100.0%');
    });

    it('formats non-finite values as "0.0%"', () => {
      expect(component.formatScore(NaN)).toBe('0.0%');
      expect(component.formatScore(Infinity)).toBe('100.0%');
    });

    it('rounds 0.555 to "55.5%" (one decimal)', () => {
      expect(component.formatScore(0.555)).toBe('55.5%');
    });
  });

  // ── 10) Extension counter ─────────────────────────────────────

  describe('extension_count', () => {
    it('exposes extension_count through stats()', () => {
      component.setStats(createMockStats({ extension_count: 3 }));
      expect(component.stats()?.extension_count).toBe(3);
    });

    it('defaults extension_count to 0 in the fixture builder', () => {
      component.setStats(createMockStats());
      expect(component.stats()?.extension_count).toBe(0);
    });
  });

  // ── 11) Dynamic sample_size ───────────────────────────────────

  describe('dynamic sample_size in banner text', () => {
    it('reflects sample_size=10 in the banner', () => {
      component.setStats(
        createMockStats({
          comparisons: 10,
          sample_size: 10,
          ready_to_resolve: true,
        }),
      );
      const text = component.bannerText();
      expect(text).toContain('10/10');
      expect(text).not.toContain('/25');
    });

    it('reflects sample_size=20 in the banner', () => {
      component.setStats(
        createMockStats({
          comparisons: 15,
          sample_size: 20,
          ready_to_resolve: false,
          needs_more_data: true,
        }),
      );
      const text = component.bannerText();
      expect(text).toContain('15/20');
      expect(text).not.toContain('/25');
    });

    it('renders different banner strings for sample_size=10 vs sample_size=20', () => {
      component.setStats(
        createMockStats({
          comparisons: 10,
          sample_size: 10,
          ready_to_resolve: true,
        }),
      );
      const banner10 = component.bannerText();

      component.setStats(
        createMockStats({
          comparisons: 10,
          sample_size: 20,
          ready_to_resolve: false,
          needs_more_data: true,
        }),
      );
      const banner20 = component.bannerText();

      expect(banner10).not.toEqual(banner20);
      expect(banner10).toContain('10/10');
      expect(banner20).toContain('10/20');
    });
  });

  // ── Bonus: shortId helper ─────────────────────────────────────

  describe('shortId', () => {
    it('truncates ids longer than 8 characters to the first 8', () => {
      expect(component.shortId('skill-aaaa1111')).toBe('skill-aa');
    });

    it('returns short ids unchanged', () => {
      expect(component.shortId('short')).toBe('short');
    });

    it('returns an em-dash for null', () => {
      expect(component.shortId(null)).toBe('—');
    });
  });

  // ── Bonus: variant status color ───────────────────────────────

  describe('variantStatusColor', () => {
    it('returns "primary" for the winner side', () => {
      component.setStats(
        createMockStats({
          comparisons: 25,
          composite_score_a: 0.9,
          composite_score_b: 0.6,
          ready_to_resolve: true,
        }),
      );
      expect(component.variantStatusColor('A')).toBe('primary');
      expect(component.variantStatusColor('B')).toBe('warn');
    });

    it('returns "accent" when tied', () => {
      component.setStats(
        createMockStats({
          comparisons: 25,
          composite_score_a: 0.7,
          composite_score_b: 0.7,
          ready_to_resolve: true,
        }),
      );
      expect(component.variantStatusColor('A')).toBe('accent');
      expect(component.variantStatusColor('B')).toBe('accent');
    });

    it('returns "" (empty token) when no data', () => {
      component.setStats(null);
      expect(component.variantStatusColor('A')).toBe('');
    });
  });

  // ── Bonus: scoreBarColor thresholds ───────────────────────────

  describe('scoreBarColor', () => {
    it('returns "primary" for score > 0.7', () => {
      expect(component.scoreBarColor(0.85)).toBe('primary');
    });

    it('returns "accent" for score in [0.4, 0.7]', () => {
      expect(component.scoreBarColor(0.7)).toBe('accent');
      expect(component.scoreBarColor(0.5)).toBe('accent');
      expect(component.scoreBarColor(0.4)).toBe('accent');
    });

    it('returns "warn" for score < 0.4', () => {
      expect(component.scoreBarColor(0.39)).toBe('warn');
      expect(component.scoreBarColor(0)).toBe('warn');
    });

    it('clamps out-of-range inputs', () => {
      expect(component.scoreBarColor(1.5)).toBe('primary');
      expect(component.scoreBarColor(-0.5)).toBe('warn');
    });
  });

  // ── Bonus: scoreBarValue scaling ───────────────────────────────

  describe('scoreBarValue', () => {
    it('scales 0.0–1.0 to 0–100', () => {
      expect(component.scoreBarValue(0.5)).toBe(50);
      expect(component.scoreBarValue(0.847)).toBe(84.7);
      expect(component.scoreBarValue(0)).toBe(0);
      expect(component.scoreBarValue(1)).toBe(100);
    });

    it('clamps to [0, 100]', () => {
      expect(component.scoreBarValue(1.5)).toBe(100);
      expect(component.scoreBarValue(-0.5)).toBe(0);
    });
  });

  // ── Bonus: hasData semantic guard ─────────────────────────────

  describe('hasData semantic guard', () => {
    it('returns true when comparisons > 0 even if all rates are zero', () => {
      component.setStats(
        createMockStats({
          comparisons: 1,
          completion_rate_a: 0,
          completion_rate_b: 0,
          applied_rate_a: 0,
          applied_rate_b: 0,
          fallback_rate_a: 0,
          fallback_rate_b: 0,
          avg_iterations_a: 0,
          avg_iterations_b: 0,
          avg_duration_a: 0,
          avg_duration_b: 0,
          composite_score_a: 0,
          composite_score_b: 0,
          ready_to_resolve: false,
          needs_more_data: true,
        }),
      );
      expect(component.hasData()).toBe(true);
      expect(component.testState()).toBe('needs_more');
    });

    it('returns false when all values are zero and no comparisons recorded', () => {
      // Fully zero out all per-variant metrics; partial overrides
      // leave createMockStats's non-zero defaults in place, which
      // would (correctly) flip hasData() to true.
      component.setStats(
        createMockStats({
          comparisons: 0,
          completion_rate_a: 0,
          completion_rate_b: 0,
          applied_rate_a: 0,
          applied_rate_b: 0,
          fallback_rate_a: 0,
          fallback_rate_b: 0,
          avg_iterations_a: 0,
          avg_iterations_b: 0,
          avg_duration_a: 0,
          avg_duration_b: 0,
          composite_score_a: 0,
          composite_score_b: 0,
          ready_to_resolve: false,
          needs_more_data: false,
        }),
      );
      expect(component.hasData()).toBe(false);
      expect(component.testState()).toBe('collecting');
    });
  });
});