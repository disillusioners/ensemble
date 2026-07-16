import {
  Component,
  signal,
  computed,
  inject,
  DestroyRef,
  effect,
  output,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatCardModule } from '@angular/material/card';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatExpansionModule } from '@angular/material/expansion';
import { map } from 'rxjs/operators';
import { SkillService } from '../../../services/skill.service';
import { SkillLineageTreeComponent } from '../../../components/skill-lineage-tree/skill-lineage-tree.component';
import { AbTestDashboardComponent } from '../../../components/ab-test-dashboard/ab-test-dashboard.component';
import { SkillUsageTableComponent } from '../../../components/skill-usage-table/skill-usage-table.component';
import { SkillTriggerListComponent } from '../../../components/skill-trigger-list/skill-trigger-list.component';
import {
  SkillDetail,
  SkillMetrics,
  SkillLineage,
  SkillAbTestStats,
  SkillTrigger,
  SkillTriggerCreate,
  SkillTriggerUpdate,
  getStatusColor,
  getStatusIcon,
  getStatusLabel,
  getCategoryColor,
  getCategoryIcon,
  getSuccessRateColor,
  formatSuccessRate,
} from '../../../models/skill.model';

@Component({
  selector: 'app-skill-detail',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatCardModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatProgressBarModule,
    MatFormFieldModule,
    MatInputModule,
    MatExpansionModule,
    SkillLineageTreeComponent,
    AbTestDashboardComponent,
    SkillUsageTableComponent,
    SkillTriggerListComponent,
  ],
  templateUrl: './skill-detail.component.html',
  styleUrl: './skill-detail.component.scss',
})
export class SkillDetailComponent {
  private readonly skillService = inject(SkillService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);

  /**
   * Skill id sourced from the ``:id`` route param. Routed via
   * ``ActivatedRoute.paramMap`` rather than an ``input.required``
   * signal because the route does not use ``withComponentInputBinding``
   * and even if it did the route param name (``id``) and input name
   * (``skillId``) would not match. Exposed as a signal so the
   * existing template bindings (``skillId()``) keep working without
   * a parent-to-child input wiring.
   */
  readonly skillId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('id') ?? '')),
    { initialValue: '' },
  );

  readonly closed = output<void>();
  readonly edit = output<string>();
  readonly fix = output<string>();
  readonly share = output<string>();
  readonly deactivate = output<string>();

  readonly skill = signal<SkillDetail | null>(null);
  readonly metrics = signal<SkillMetrics | null>(null);
  readonly lineage = signal<SkillLineage | null>(null);
  /** Per-variant comparison stats from ``GET /api/skills/{id}/ab-test/stats``. */
  readonly abTestStats = signal<SkillAbTestStats | null>(null);
  /** Triggers for the skill's project scope (or globals for global skills). */
  readonly triggers = signal<SkillTrigger[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  // Show full content vs collapsed (toggle button)
  readonly contentExpanded = signal(false);
  // Feedback form (for the bottom panel)
  readonly feedbackHelpful = signal(true);
  readonly feedbackText = signal('');
  readonly feedbackSubmitting = signal(false);

  readonly hasLineage = computed(() => {
    const l = this.lineage();
    return !!(l && (l.parents.length > 0 || l.children.length > 0));
  });

  readonly statusColor = computed(() =>
    this.skill() ? getStatusColor(this.skill()!.status) : '#9ca3af',
  );
  readonly statusIcon = computed(() =>
    this.skill() ? getStatusIcon(this.skill()!.status) : 'help',
  );
  readonly statusLabel = computed(() =>
    this.skill() ? getStatusLabel(this.skill()!.status) : '',
  );
  readonly categoryColor = computed(() =>
    this.skill() ? getCategoryColor(this.skill()!.category) : '#6b7280',
  );
  readonly categoryIcon = computed(() =>
    this.skill() ? getCategoryIcon(this.skill()!.category) : 'category',
  );

  readonly successRate = computed(() => {
    const m = this.metrics();
    if (!m) return 0;
    return m.completion_rate;
  });
  readonly successRateColor = computed(() => getSuccessRateColor(this.successRate()));
  readonly successRateDisplay = computed(() => formatSuccessRate(this.successRate()));
  readonly fallbackRateDisplay = computed(() => {
    const m = this.metrics();
    return m ? formatSuccessRate(m.fallback_rate) : '0%';
  });

  /**
   * Composite score for the current skill's A/B variant, or ``null``
   * when the skill is not enrolled in an active test (or the variant
   * mapping is missing from the stats payload).
   *
   * The backend maps the incumbent → ``skill_id_a`` and the
   * challenger → ``skill_id_b``; we look up which side this skill
   * sits on by id-match so the metrics tile can render the right
   * composite without a second call.
   */
  readonly compositeScore = computed<{ variant: 'A' | 'B'; score: number } | null>(() => {
    const stats = this.abTestStats();
    const id = this.skillId();
    if (!stats || !id) {
      return null;
    }
    if (stats.skill_id_a === id) {
      return { variant: 'A', score: stats.composite_score_a };
    }
    if (stats.skill_id_b === id) {
      return { variant: 'B', score: stats.composite_score_b };
    }
    return null;
  });

  constructor() {
    effect(() => {
      const id = this.skillId();
      if (id) {
        this.loadAll(id);
      }
    });
  }

  protected loadAll(id: string): void {
    this.loading.set(true);
    this.error.set(null);

    // Load detail
    this.skillService.get(id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (detail) => {
        this.skill.set(detail);
        this.loading.set(false);
        // Trigger load depends on the skill's project scope — fire
        // it AFTER the detail resolves. The standalone ``listTriggers``
        // service method filters by ``project_id`` (or returns
        // globals when omitted); we forward the skill's project so
        // the detail page shows triggers relevant to its scope.
        this.loadTriggers(detail.project_id ?? null);
      },
      error: (err: Error) => {
        console.error('Failed to load skill detail:', err);
        this.error.set(err.message || 'Failed to load skill');
        this.loading.set(false);
      },
    });

    // Load metrics (non-fatal)
    this.skillService
      .getMetrics(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (m) => this.metrics.set(m),
        error: () => {
          /* non-fatal */
        },
      });

    // Load lineage (non-fatal)
    this.skillService
      .getLineage(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (l) => this.lineage.set(l),
        error: () => {
          /* non-fatal */
        },
      });

    // Load A/B test comparison stats (non-fatal — most skills are
    // not enrolled in a test so the envelope yields ``stats: null``).
    this.loadAbTestStats(id);
  }

  /** Re-fetch the A/B comparison stats for the current skill. */
  private loadAbTestStats(id: string): void {
    this.skillService
      .getAbTestStats(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => this.abTestStats.set(response.stats ?? null),
        error: () => this.abTestStats.set(null),
      });
  }

  /** Re-fetch triggers for the skill's project scope. */
  private loadTriggers(projectId: string | null): void {
    // ``undefined`` makes the backend return globals; a non-null
    // ``projectId`` scopes to that project. We pass
    // ``projectId ?? undefined`` so a ``null`` skill scope yields
    // global triggers instead of an empty list.
    //
    // ``enabledOnly: false`` returns ALL triggers (enabled and
    // disabled) — the trigger list has an enable/disable toggle so
    // filtering disabled ones out server-side would make them
    // disappear from the UI as soon as the user flips them off.
    this.skillService
      .listTriggers(projectId ?? undefined, false)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (triggers) => this.triggers.set(triggers),
        error: () => this.triggers.set([]),
      });
  }

  protected onBack(): void {
    this.closed.emit();
  }

  protected onEdit(): void {
    this.edit.emit(this.skillId());
  }

  protected onFix(): void {
    this.fix.emit(this.skillId());
  }

  protected onShare(): void {
    this.share.emit(this.skillId());
  }

  protected onDeactivate(): void {
    this.deactivate.emit(this.skillId());
  }

  protected onToggleContent(): void {
    this.contentExpanded.update((v) => !v);
  }

  protected onSubmitFeedback(): void {
    if (this.feedbackSubmitting()) return;
    this.feedbackSubmitting.set(true);
    // The detail page does not have ``instance_id`` / ``agent_id``
    // context — both are optional query params on the backend, so
    // we omit them and let the server handle the absence gracefully.
    this.skillService
      .submitFeedback(this.skillId(), {
        applied: this.feedbackHelpful(),
        note: this.feedbackText().trim() || undefined,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.feedbackSubmitting.set(false);
          this.snackBar.open('Feedback submitted', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.feedbackText.set('');
        },
        error: (err: Error) => {
          this.feedbackSubmitting.set(false);
          this.snackBar.open(err.message || 'Failed to submit feedback', 'Dismiss', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  protected onResolveAbTest(): void {
    // The backend picks the winner server-side — the dashboard emits
    // a parameterless ``resolve`` event (the variant split lives on
    // ``SkillAbTestStats``). On success we refresh the comparison
    // stats so the dashboard re-renders with the resolved payload;
    // a full ``loadAll`` would also clobber the user's expansion
    // state on the lineage / usage / trigger panels, so we only
    // refresh the data that actually changed.
    this.skillService
      .resolveAbTest(this.skillId())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          const reason = result.resolved
            ? `A/B test resolved${result.winner_id ? ` (winner ${result.winner_id})` : ''}`
            : 'A/B test resolution pending';
          this.snackBar.open(reason, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.loadAbTestStats(this.skillId());
        },
        error: (err: Error) => {
          this.snackBar.open(err.message || 'Failed to resolve A/B test', 'Dismiss', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  /**
   * Navigate to a related skill when the user clicks a node in the
   * lineage tree. The tree component emits the target skill id; we
   * delegate to the existing ``Router`` so deep links work the same
   * way as the rest of the app.
   */
  protected onNavigateTo(skillId: string): void {
    this.router.navigate(['/skills', skillId]);
  }

  // ── Trigger CRUD handlers ─────────────────────────────────────────
  //
  // The SkillTriggerListComponent is event-driven only — it emits
  // ``create`` / ``update`` / ``delete`` and we forward each to the
  // SkillService. On success we refresh the local list and surface a
  // snackbar; on failure we surface the backend error message.

  protected onCreateTrigger(data: SkillTriggerCreate): void {
    // Default the new trigger to the skill's project scope when the
    // form dialog left ``project_id`` undefined, so a project-scoped
    // skill naturally creates project triggers.
    const skillScope = this.skill()?.project_id ?? null;
    const payload: SkillTriggerCreate = {
      ...data,
      project_id: data.project_id ?? skillScope,
    };
    this.skillService
      .createTrigger(payload)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.snackBar.open('Trigger created', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.loadTriggers(skillScope);
        },
        error: (err: Error) => {
          this.snackBar.open(err.message || 'Failed to create trigger', 'Dismiss', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  protected onUpdateTrigger(payload: { id: string; data: SkillTriggerUpdate }): void {
    this.skillService
      .updateTrigger(payload.id, payload.data)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.snackBar.open('Trigger updated', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.loadTriggers(this.skill()?.project_id ?? null);
        },
        error: (err: Error) => {
          this.snackBar.open(err.message || 'Failed to update trigger', 'Dismiss', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  protected onDeleteTrigger(id: string): void {
    const skillScope = this.skill()?.project_id ?? null;
    this.skillService
      .deleteTrigger(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          const msg = result.deleted
            ? 'Trigger deleted'
            : 'Trigger was already removed';
          this.snackBar.open(msg, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.loadTriggers(skillScope);
        },
        error: (err: Error) => {
          this.snackBar.open(err.message || 'Failed to delete trigger', 'Dismiss', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  protected formatDate(ts: string | null): string {
    if (!ts) return 'N/A';
    return new Date(ts).toLocaleString();
  }
}