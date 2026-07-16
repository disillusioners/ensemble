# Phase 4: A/B Test Analytics Dashboard (Component Build)

## Objective
Build an analytics dashboard component that shows composite scores per variant, per-metric comparison, winner indication, and "ready to resolve" status. Replaces the basic status display with a data-rich dashboard. **This phase builds a standalone component only — integration into `skill-detail.component.html` happens in Phase 6.**

## Coupling
- **Depends on**: Phase 2 (tight — imports `SkillAbTestStats`, `SkillAbTestStatsResponse`)
- **Coupling type**: tight
- **Shared files with other phases**: New component files only. **Does NOT modify `skill-detail.component.html`** (deferred to Phase 6).
- **Shared APIs/interfaces**: Consumes `SkillAbTestStats` interface (enriched per Phase 1 Task 3/4 + Phase 2 Task 8); calls `skill.service.getAbTestStats()` from Phase 2
- **Why this coupling**: Must have the `SkillAbTestStats` interface with per-variant metrics from Phase 2 to type the response.
- **Parallel safety**: This phase creates only NEW files. Phases 3 and 5 also create only NEW files. All three can run in parallel without file conflicts.

## Context
- Phase 1 Task 3 extended `get_ab_comparison_stats()` to return per-variant `applied_rate`, `fallback_rate`, `avg_iterations`, `avg_duration` (W2 fix)
- Phase 1 Task 4 added `sample_size` to the response (W3 fix)
- Phase 2 added `SkillAbTestStats` interface with all enriched fields + `skill.service.getAbTestStats()`
- Composite score formula: `(completion_rate × 0.35) + (applied_rate × 0.20) + (efficiency × 0.20) + (low_fallback × 0.15) + (speed × 0.10)`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `AbTestDashboardComponent` | Standalone component. Inputs: `skillId: input.required<string>()`, `abTestStatus: input<AbTestStatus\|null>(null)`. Fetches stats via `skill.service.getAbTestStats()`. | `frontend/src/app/components/ab-test-dashboard/ab-test-dashboard.component.ts/html/scss` — **NEW** |
| 2 | Implement score comparison cards | Two side-by-side cards (Variant A vs Variant B) showing: composite score (0-1, displayed as %), completion rate, applied rate, fallback rate, avg iterations, avg duration. Highlight winner with colored accent. | Inside component |
| 3 | Implement per-metric breakdown table | **[W2]** Uses enriched per-variant fields from `SkillAbTestStats`: `applied_rate_a/b`, `fallback_rate_a/b`, `avg_iterations_a/b`, `avg_duration_a/b`. Table: Metric \| Variant A \| Variant B \| Difference. | Inside component |
| 4 | Add composite score progress bars | Use `mat-progress-bar` for each variant's composite score. Color-code: green > 0.7, yellow 0.4-0.7, red < 0.4. | Inside component |
| 5 | **[W3]** Implement ready-to-resolve banner with dynamic sample size | If `ready_to_resolve === true`, show banner: "A/B test has sufficient data ({{ comparisons }}/{{ sample_size }} comparisons, {{ difference }}% score difference)." Uses `stats.sample_size` — NOT hardcoded `/10`. | Inside component |
| 6 | **[W3]** Implement "needs more data" state with dynamic sample size | If `needs_more_data === true`, show info: "{{ comparisons }}/{{ sample_size }} comparisons needed before resolution." | Inside component |
| 7 | Show winner visualization | When A/B test is resolved, display prominent winner card with winner name, winning score, margin of victory. | Inside component |

## Key Files
- `frontend/src/app/components/ab-test-dashboard/ab-test-dashboard.component.ts/html/scss` — **NEW** main component
- `frontend/src/app/services/skill.service.ts` — **REFERENCE** uses `getAbTestStats()` from Phase 2

> **Note**: `skill-detail.component.html` and `skill-detail.component.ts` are NOT modified in this phase. Integration happens in Phase 6.

## Component Design

### AbTestDashboardComponent
```typescript
@Component({
  selector: 'app-ab-test-dashboard',
  standalone: true,
  imports: [
    CommonModule, MatCardModule, MatProgressBarModule, MatTableModule,
    MatIconModule, MatButtonModule, MatTooltipModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="ab-dashboard">
      @if (loading()) {
        <mat-spinner diameter="40" />
      } @else if (stats()) {
        @if (stats()!.ready_to_resolve) {
          <div class="ready-banner">
            <mat-icon>check_circle</mat-icon>
            Ready to resolve: {{ stats()!.comparisons }}/{{ stats()!.sample_size }} comparisons,
            {{ formatPercent(stats()!.difference) }} score difference
          </div>
        }
        @if (stats()!.needs_more_data) {
          <div class="info-banner">
            <mat-icon>info</mat-icon>
            More data needed: {{ stats()!.comparisons }}/{{ stats()!.sample_size }} comparisons
          </div>
        }

        <!-- Score comparison cards -->
        <div class="score-cards">
          <mat-card class="variant-card" [class.winner]="isVariantAWinner()">
            <mat-card-header>
              <mat-card-title>Variant A</mat-card-title>
              <mat-card-subtitle>{{ stats()!.skill_id_a?.substring(0, 8) }}</mat-card-subtitle>
            </mat-card-header>
            <mat-card-content>
              <div class="composite-score">
                <span class="score-value">{{ formatPercent(stats()!.composite_score_a) }}</span>
                <span class="score-label">Composite Score</span>
              </div>
              <mat-progress-bar mode="determinate"
                [value]="stats()!.composite_score_a * 100"
                [color]="getScoreColor(stats()!.composite_score_a)" />
            </mat-card-content>
          </mat-card>

          <mat-card class="variant-card" [class.winner]="isVariantBWinner()">
            <mat-card-title>Variant B</mat-card-title>
            <!-- composite_score_b, progress bar, etc. -->
          </mat-card>
        </div>

        <!-- [W2] Per-metric breakdown table using enriched fields -->
        <mat-table [dataSource]="metricRows()">
          <ng-container matColumnDef="metric">
            <mat-header-cell *matHeaderCellDef>Metric</mat-header-cell>
            <mat-cell *matCellDef="let row">{{ row.metric }}</mat-cell>
          </ng-container>
          <ng-container matColumnDef="variantA">
            <mat-header-cell *matHeaderCellDef>Variant A</mat-header-cell>
            <mat-cell *matCellDef="let row">{{ formatValue(row.metric, row.a) }}</mat-cell>
          </ng-container>
          <ng-container matColumnDef="variantB">
            <mat-header-cell *matHeaderCellDef>Variant B</mat-header-cell>
            <mat-cell *matCellDef="let row">{{ formatValue(row.metric, row.b) }}</mat-cell>
          </ng-container>
          <ng-container matColumnDef="diff">
            <mat-header-cell *matHeaderCellDef>Δ</mat-header-cell>
            <mat-cell *matCellDef="let row">{{ formatValue(row.metric, row.b - row.a) }}</mat-cell>
          </ng-container>
          <mat-header-row *matHeaderRowDef="displayedColumns"></mat-header-row>
          <mat-row *matRowDef="let row; columns: displayedColumns;"></mat-row>
        </mat-table>
      } @else {
        <p class="no-data">No active A/B test for this skill.</p>
      }
    </div>
  `,
})
export class AbTestDashboardComponent implements OnInit {
  skillId = input.required<string>();
  abTestStatus = input<AbTestStatus | null>(null);

  stats = signal<SkillAbTestStats | null>(null);
  loading = signal(false);

  displayedColumns = ['metric', 'variantA', 'variantB', 'diff'];

  constructor(private skillService: SkillService) {}

  ngOnInit() {
    if (this.abTestStatus()) {
      this.loadStats();
    }
  }

  private loadStats() {
    this.loading.set(true);
    this.skillService.getAbTestStats(this.skillId()).subscribe({
      next: (response) => {
        this.stats.set(response.stats);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  isVariantAWinner = computed(() => {
    const s = this.stats();
    return s ? s.composite_score_a > s.composite_score_b : false;
  });

  isVariantBWinner = computed(() => {
    const s = this.stats();
    return s ? s.composite_score_b > s.composite_score_a : false;
  });

  // [W2] Uses enriched per-variant fields from SkillAbTestStats
  metricRows = computed(() => {
    const s = this.stats();
    if (!s) return [];
    return [
      { metric: 'Composite Score',  a: s.composite_score_a,  b: s.composite_score_b },
      { metric: 'Completion Rate',  a: s.completion_rate_a,  b: s.completion_rate_b },
      { metric: 'Applied Rate',     a: s.applied_rate_a,     b: s.applied_rate_b },
      { metric: 'Fallback Rate',    a: s.fallback_rate_a,    b: s.fallback_rate_b },
      { metric: 'Avg Iterations',   a: s.avg_iterations_a,   b: s.avg_iterations_b },
      { metric: 'Avg Duration',     a: s.avg_duration_a,     b: s.avg_duration_b },
    ];
  });

  // [W3] Uses sample_size from stats, NOT hardcoded
  formatPercent(value: number): string {
    return (value * 100).toFixed(1) + '%';
  }

  formatValue(metric: string, value: number): string {
    if (metric.includes('Rate') || metric.includes('Score')) {
      return this.formatPercent(value);
    }
    if (metric.includes('Duration')) {
      return value.toFixed(1) + 's';
    }
    return value.toFixed(1);
  }

  getScoreColor(score: number): string {
    if (score > 0.7) return 'primary';
    if (score > 0.4) return 'accent';
    return 'warn';
  }
}
```

## Constraints
- Use Angular Material for all visual elements (consistent with app theme)
- Composite scores displayed as percentages (0-100%) with 1 decimal place
- Color thresholds: green > 0.7, yellow 0.4-0.7, red < 0.4
- **[W3]** Use `stats.sample_size` for comparison denominators — NEVER hardcode `/10`
- **[W2]** Per-metric breakdown uses enriched `SkillAbTestStats` fields (not extra round-trips)
- Loading and error states must be clear
- Mobile-responsive (cards stack on narrow screens)

## Testing Strategy
- Unit test score formatting (0.847 → "84.7%")
- Unit test winner determination logic
- **[W3]** Unit test that banner text uses `sample_size` dynamically (mock different sample_size values, verify text changes)
- Component test: stats loading, loading state, error state, null stats (no active test)
- Component test: metricRows computed signal returns correct enriched fields

## Deliverables
- [ ] `AbTestDashboardComponent` created with all sub-features
- [ ] **[W2]** Per-metric breakdown table using enriched fields from `SkillAbTestStats`
- [ ] **[W3]** Ready-to-resolve and needs-more-data banners use `sample_size` dynamically
- [ ] Winner visualization on resolved tests
- [ ] Component tests passing
- [ ] `ng build` compiles

> **Integration into skill-detail page is deferred to Phase 6.**
