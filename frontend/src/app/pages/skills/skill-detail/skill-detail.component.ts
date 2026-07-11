import {
  Component,
  signal,
  computed,
  inject,
  OnInit,
  OnDestroy,
  input,
  output,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { SkillService } from '../../../services/skill.service';
import {
  SkillDetail,
  SkillMetrics,
  SkillLineage,
  AbTestStatus,
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
    MatDividerModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatProgressBarModule,
  ],
  templateUrl: './skill-detail.component.html',
  styleUrl: './skill-detail.component.scss',
})
export class SkillDetailComponent implements OnInit, OnDestroy {
  private readonly skillService = inject(SkillService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);

  readonly skillId = input.required<string>();

  readonly closed = output<void>();
  readonly edit = output<string>();
  readonly fix = output<string>();
  readonly share = output<string>();
  readonly deactivate = output<string>();

  readonly skill = signal<SkillDetail | null>(null);
  readonly metrics = signal<SkillMetrics | null>(null);
  readonly lineage = signal<SkillLineage | null>(null);
  readonly abTest = signal<AbTestStatus | null>(null);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  // Show full content vs collapsed (toggle button)
  readonly contentExpanded = signal(false);
  // Feedback form (for the bottom panel)
  readonly feedbackHelpful = signal(true);
  readonly feedbackText = signal('');
  readonly feedbackSubmitting = signal(false);

  readonly hasAbTest = computed(() => this.abTest() !== null);
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

  constructor() {
    effect(() => {
      const id = this.skillId();
      if (id) {
        this.loadAll(id);
      }
    });
  }

  ngOnInit(): void {}

  ngOnDestroy(): void {}

  protected loadAll(id: string): void {
    this.loading.set(true);
    this.error.set(null);

    // Load detail
    this.skillService.get(id).subscribe({
      next: (detail) => this.skill.set(detail),
      error: (err: Error) => {
        console.error('Failed to load skill detail:', err);
        this.error.set(err.message || 'Failed to load skill');
        this.loading.set(false);
      },
    });

    // Load metrics (non-fatal)
    this.skillService.getMetrics(id).subscribe({
      next: (m) => this.metrics.set(m),
      error: () => {
        /* non-fatal */
      },
    });

    // Load lineage (non-fatal)
    this.skillService.getLineage(id).subscribe({
      next: (l) => this.lineage.set(l),
      error: () => {
        /* non-fatal */
      },
    });

    // Load A/B test status (may return null)
    this.skillService.getAbTestStatus(id).subscribe({
      next: (status) => {
        this.abTest.set(status);
        this.loading.set(false);
      },
      error: () => {
        this.abTest.set(null);
        this.loading.set(false);
      },
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

  protected onResolveAbTest(_winnerId: string): void {
    // The backend picks the winner server-side now — we forward the
    // A/B test anchor id only. ``_winnerId`` is kept in the call
    // signature so the template / event bindings can stay
    // unchanged; it is intentionally unused.
    this.skillService.resolveAbTest(this.skillId()).subscribe({
      next: (result) => {
        const reason = result.resolved
          ? `A/B test resolved${result.winner_id ? ` (winner ${result.winner_id})` : ''}`
          : 'A/B test resolution pending';
        this.snackBar.open(reason, 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.loadAll(this.skillId());
      },
      error: (err: Error) => {
        this.snackBar.open(err.message || 'Failed to resolve A/B test', 'Dismiss', {
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