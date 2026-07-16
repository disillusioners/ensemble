import { Component, input, output, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { SkillAbTestStats } from '../../models/skill.model';

/**
 * Composite-score metric row used by `metricRows()`.
 *
 * Each row represents one of the five weighted signals blended into
 * `composite_score_a` / `composite_score_b` on the backend (see
 * `daemon/services/skill_metrics_service.py:_composite_score`). The
 * weights mirror the backend defaults:
 *
 * | Metric          | Weight | Notes                                  |
 * |-----------------|--------|----------------------------------------|
 * | Completion Rate | 0.35   | higher is better                       |
 * | Applied Rate    | 0.20   | higher is better                       |
 * | Efficiency      | 0.20   | derived from avg_iterations            |
 * | Low Fallback    | 0.15   | `1 - fallback_rate`; higher is better  |
 * | Speed           | 0.10   | derived from avg_duration              |
 *
 * `scoreA` / `scoreB` are normalised into `[0.0, 1.0]` per metric
 * (the same way the backend pre-normalises them before applying
 * the weights) so the table can render the weighted contribution
 * (`score × weight`) instead of raw rates.
 */
interface MetricRow {
  name: string;
  weight: number;
  scoreA: number;    // normalised 0.0–1.0
  scoreB: number;    // normalised 0.0–1.0
  displayA: string;  // human-readable A value (raw rate or count)
  displayB: string;  // human-readable B value (raw rate or count)
  winner: 'A' | 'B' | 'tie';
  higherIsBetter: boolean;
}

/**
 * Possible test states derived from the `SkillAbTestStats` payload.
 *
 * * `no_data`     — `stats === null`, no active A/B test.
 * * `collecting`  — test exists but no comparisons recorded yet
 *                   (all per-variant rates are 0 and
 *                   `comparisons === 0`).
 * * `needs_more`  — `needs_more_data === true`, comparisons below
 *                   `sample_size`.
 * * `ready`       — `ready_to_resolve === true`, test can be
 *                   resolved.
 */
export type TestState = 'no_data' | 'collecting' | 'needs_more' | 'ready';

/**
 * Possible winner signals exposed by `winnerVariant()`.
 *
 * `null` is returned when both scores are missing (no test data
 * yet). Per the backend tie-breaking rule (see
 * `daemon/services/skill_evolution_service.py:_pick_winner`) the
 * challenger (Variant B) wins ties — `winnerVariant()` mirrors
 * that so the UI never disagrees with the resolver.
 */
export type WinnerVariant = 'A' | 'B' | 'tie' | null;

/**
 * Presentational dashboard for an active Skill A/B test.
 *
 * Renders the comparison stats supplied by
 * `GET /api/skills/{id}/ab-test/stats` (see
 * `SkillAbTestStats` in `models/skill.model.ts`). The dashboard is
 * intentionally **read-only on data**: it receives the stats via
 * the `stats` input and emits a single `resolve` event when the
 * operator chooses to resolve the test. The owning page
 * (`SkillDetailComponent` in a follow-up phase) is responsible
 * for making the HTTP call and reacting to the resolve outcome.
 *
 * Tie-breaking follows the backend rule (B wins ties — see
 * `skill_evolution_service._pick_winner`). Tests live in
 * `ab-test-dashboard.component.spec.ts`.
 *
 * Display elements:
 *
 * 1. Two-column variant header (Variant A "incumbent" vs Variant
 *    B "challenger") with a derived status badge per variant.
 * 2. Side-by-side composite-score progress bar with colour
 *    thresholds (green > 0.7, amber 0.4–0.7, red < 0.4).
 * 3. Five-row per-metric breakdown table with weights, raw values
 *    and a winner indicator per row.
 * 4. Banner derived from `ready_to_resolve` / `needs_more_data` /
 *    `comparisons` / `sample_size`.
 * 5. Resolve button (only when `ready_to_resolve === true`) plus
 *    extension counter.
 *
 * Edge cases:
 *
 * * `stats === null` → "No active A/B test" empty state.
 * * All per-variant metrics are 0 → "Collecting data..." skeleton
 *   instead of misleading 0% scores.
 * * Tied composite scores → "Tied" badge + tooltip noting that
 *   the challenger (Variant B) wins ties.
 */
@Component({
  selector: 'app-ab-test-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatProgressBarModule,
    MatTableModule,
    MatTooltipModule,
  ],
  templateUrl: './ab-test-dashboard.component.html',
  styleUrl: './ab-test-dashboard.component.scss',
})
export class AbTestDashboardComponent {
  // ── Inputs ────────────────────────────────────────────────────────

  /** Live A/B comparison stats. `null` indicates "no active test". */
  readonly stats = input<SkillAbTestStats | null>(null);

  /** Skill display name for the header — purely cosmetic. */
  readonly skillName = input<string>('');

  // ── Output ───────────────────────────────────────────────────────

  /** Emitted when the operator clicks the Resolve button. */
  readonly resolve = output<void>();

  // ── Display columns for the per-metric table ─────────────────────

  protected readonly displayedColumns = ['name', 'weight', 'a', 'b', 'winner'];

  // ── Backend-derived constants ────────────────────────────────────
  //
  // Documented in skill_metrics_service._composite_score as
  // SkillEvolutionConfig defaults — kept local so this component
  // does not import daemon code.

  /**
   * Normalisation constants for the efficiency and speed metrics.
   *
   * These are NOT present in the `SkillAbTestStats` payload (the
   * backend applies its `1 - (x / baseline)` formula before
   * weighting), so the FE has to pick a reasonable normalisation
   * on its own. Both helpers below use the same inverse curve
   * (`1 / (1 + x)`) which:
   *
   *   * stays bounded in `[0.0, 1.0]` for any non-negative input,
   *   * returns `1.0` when the input is `0` (perfect score),
   *   * smoothly decays toward `0` as the input grows.
   *
   * This is a display-only approximation — the canonical
   * composite scores live in `composite_score_a` /
   * `composite_score_b` and do NOT need re-derivation.
   */
  protected static normaliseInverse(x: number): number {
    if (!isFinite(x) || x < 0) return 0;
    return 1 / (1 + x);
  }

  // ── Computed signals ─────────────────────────────────────────────

  /**
   * Whether the underlying payload has any real data. Used to swap
   * the dashboard for the "Collecting data..." skeleton when
   * `comparisons === 0` and every per-variant rate is 0.
   *
   * The "or any metric non-zero" guard handles the edge case where
   * the backend has recorded comparisons (so `comparisons > 0`)
   * but the rates have not yet been aggregated — we want the
   * dashboard visible in that case instead of flashing skeleton.
   */
  readonly hasData = computed<boolean>(() => {
    const s = this.stats();
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

  /**
   * Coarse test state used by the template to pick the right
   * banner + controls. `no_data` is the only state that needs to
   * check `stats === null` — the other states live inside a
   * non-null payload.
   */
  readonly testState = computed<TestState>(() => {
    const s = this.stats();
    if (!s) return 'no_data';
    if (!this.hasData()) return 'collecting';
    if (s.ready_to_resolve) return 'ready';
    if (s.needs_more_data) return 'needs_more';
    // Fallback: stats exist and comparisons recorded but neither
    // backend flag was set. Treat as collecting so the UI stays
    // conservative rather than flashing a Resolve button.
    return 'collecting';
  });

  /**
   * Winner signal: `'A'` (incumbent), `'B'` (challenger),
   * `'tie'` (per backend rule B wins ties), or `null` when no
   * data yet.
   *
   * Mirror of `skill_evolution_service._pick_winner` — the
   * frontend never disagrees with the backend's resolution call.
   */
  readonly winnerVariant = computed<WinnerVariant>(() => {
    const s = this.stats();
    if (!s || !this.hasData()) return null;
    const scoreA = s.composite_score_a;
    const scoreB = s.composite_score_b;
    if (scoreA === scoreB) return 'tie';
    if (scoreB > scoreA) return 'B';
    return 'A';
  });

  /**
   * Five-row metric breakdown. Weights mirror the backend
   * `SkillEvolutionConfig` defaults (see
   * `skill_metrics_service._composite_score`).
   */
  readonly metricRows = computed<MetricRow[]>(() => {
    const s = this.stats();
    if (!s) return [];

    // Normalise raw rates into the same `[0.0, 1.0]` shape the
    // backend uses so we can render weighted contributions.
    const completionA = clamp01(s.completion_rate_a);
    const completionB = clamp01(s.completion_rate_b);
    const appliedA = clamp01(s.applied_rate_a);
    const appliedB = clamp01(s.applied_rate_b);

    // Fallback-rate is "lower is better" — flip to low_fallback
    // before weighting (matches the backend `low_fallback_rate`
    // pre-normalisation step).
    const lowFallbackA = clamp01(1 - s.fallback_rate_a);
    const lowFallbackB = clamp01(1 - s.fallback_rate_b);

    // Efficiency / speed are not present on the payload as
    // normalised scores. We pick a reasonable inverse curve
    // (`1 / (1 + x)`) so the table can render a 0–1 score column
    // consistent with the other three "raw rate" metrics. The
    // exact formula is documented on `AbTestDashboardComponent`
    // above; the canonical values are the composite scores.
    const efficiencyA = AbTestDashboardComponent.normaliseInverse(s.avg_iterations_a);
    const efficiencyB = AbTestDashboardComponent.normaliseInverse(s.avg_iterations_b);
    const speedA = AbTestDashboardComponent.normaliseInverse(s.avg_duration_a);
    const speedB = AbTestDashboardComponent.normaliseInverse(s.avg_duration_b);

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

  // ── Display helpers ──────────────────────────────────────────────

  /** Composite score clamped to `[0.0, 1.0]` and formatted. */
  protected formatScore(score: number): string {
    return formatPercent(clamp01(score));
  }

  /** Truncated 8-char id for the variant header. */
  protected shortId(id: string | null): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) : id;
  }

  /** Status chip text derived from the winner signal. */
  protected variantStatus(variant: 'A' | 'B'): 'Winner' | 'Loser' | 'Tied' | 'Active' {
    const winner = this.winnerVariant();
    if (winner === null) return 'Active';
    if (winner === 'tie') return 'Tied';
    const winnerSide: 'A' | 'B' = winner;
    return winnerSide === variant ? 'Winner' : 'Loser';
  }

  /** Material color token for a variant status chip. */
  protected variantStatusColor(variant: 'A' | 'B'): string {
    const status = this.variantStatus(variant);
    switch (status) {
      case 'Winner': return 'primary';
      case 'Loser':  return 'warn';
      case 'Tied':   return 'accent';
      default:       return '';
    }
  }

  /**
   * Progress-bar value scaled to 0–100. Clamps to `[0, 100]` so a
   * backend bug that returns a score > 1 cannot blow up the bar.
   */
  protected scoreBarValue(score: number): number {
    return clamp01(score) * 100;
  }

  /**
   * Material color token for a progress-bar fill. Mirrors the
   * status threshold legend (`green > 0.7`, amber `0.4–0.7`,
   * red `< 0.4`).
   */
  protected scoreBarColor(score: number): 'primary' | 'accent' | 'warn' {
    const clamped = clamp01(score);
    if (clamped > 0.7) return 'primary';
    if (clamped >= 0.4) return 'accent';
    return 'warn';
  }

  /**
   * Dynamic banner text. Reading `sample_size` from the payload
   * (NOT hardcoded `/10`) keeps the UI correct when operators
   * raise the threshold in `SkillEvolutionConfig`.
   */
  protected bannerText(): string {
    const s = this.stats();
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
      case 'needs_more': {
        return (
          `Collecting data: ${s.comparisons}/${s.sample_size} comparisons. ` +
          'Test is still accumulating signal.'
        );
      }
      case 'collecting':
        return `Collecting data… ${s.comparisons}/${s.sample_size} comparisons so far.`;
      default:
        return '';
    }
  }

  /** Resolve click handler — keeps the public output tidy. */
  protected onResolve(): void {
    this.resolve.emit();
  }
}

// ── Module-private helpers ─────────────────────────────────────────

function clamp01(x: number): number {
  // NaN is the "no data" sentinel → render at 0 so the UI does
  // not flash "NaN%" on a divide-by-zero in the backend.
  // ±Infinity bounds-up / -down so out-of-range inputs cannot
  // overflow the progress bar.
  if (Number.isNaN(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

/**
 * Score-formatter. Mirrors `formatPercent` tests in this file —
 * displays a `[0.0–1.0]` rate as a string with one decimal
 * followed by `%`. `0.847` → `"84.7%"`, `0` → `"0.0%"`,
 * `1` → `"100.0%"`.
 */
function formatPercent(score: number): string {
  const pct = clamp01(score) * 100;
  // `Number.toFixed(1)` always emits a single decimal place
  // ("0.0%", "100.0%") so chip widths stay consistent — a naive
  // template-literal interpolation drops the trailing zero on
  // round values and makes the column uneven.
  return `${pct.toFixed(1)}%`;
}

/**
 * Per-metric winner indicator. Returns `'A'` / `'B'` / `'tie'`.
 * Ties are reported as `'tie'` here so the table can render a
 * neutral indicator; the variant-level `winnerVariant()` then
 * applies the "B wins ties" rule for the dashboard winner.
 */
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
