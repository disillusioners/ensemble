import {
  Component,
  signal,
  inject,
  OnInit,
  OnDestroy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatCardModule } from '@angular/material/card';

import { SkillService } from '../../../services/skill.service';
import { SkillTriggerListComponent } from '../../../components/skill-trigger-list/skill-trigger-list.component';
import {
  SkillTrigger,
  SkillTriggerCreate,
  SkillTriggerUpdate,
} from '../../../models/skill.model';

/**
 * Standalone route page for the Skill Triggers surface (Phase 6).
 *
 * Wraps the presentational ``SkillTriggerListComponent`` with the
 * data-fetching + snackbar feedback plumbing so the list can be
 * reached via a dedicated ``/skills/triggers`` URL. Mirrors the
 * ``SkillBankComponent`` shape (signals-based state, snackbar
 * feedback, inline create/edit/delete via service calls) but
 * delegates the actual card rendering to the shared list component
 * so the same trigger UI can be reused inside a detail panel.
 */
@Component({
  selector: 'app-skill-triggers-page',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatCardModule,
    SkillTriggerListComponent,
  ],
  templateUrl: './skill-triggers.page.component.html',
  styleUrl: './skill-triggers.page.component.scss',
})
export class SkillTriggersPageComponent implements OnInit, OnDestroy {
  private readonly skillService = inject(SkillService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly router = inject(Router);

  /** Triggers fetched on init and refreshed after every mutation. */
  readonly triggers = signal<SkillTrigger[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.loadTriggers();
  }

  ngOnDestroy(): void {
    this.skillService.clearError();
  }

  protected onRefresh(): void {
    this.loadTriggers();
  }

  protected onBack(): void {
    this.router.navigate(['/skills']);
  }

  protected onCreate(payload: SkillTriggerCreate): void {
    this.skillService.createTrigger(payload).subscribe({
      next: () => {
        this.snackBar.open('Trigger created', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.loadTriggers();
      },
      error: (err: Error) => {
        this.showMutationError(err, 'create');
      },
    });
  }

  protected onUpdate(event: { id: string; data: SkillTriggerUpdate }): void {
    this.skillService.updateTrigger(event.id, event.data).subscribe({
      next: () => {
        this.snackBar.open('Trigger updated', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar',
        });
        this.loadTriggers();
      },
      error: (err: Error) => {
        this.showMutationError(err, 'update');
      },
    });
  }

  protected onDelete(id: string): void {
    this.skillService.deleteTrigger(id).subscribe({
      next: () => {
        this.snackBar.open('Trigger deleted', 'Close', {
          duration: 3000,
        });
        this.loadTriggers();
      },
      error: (err: Error) => {
        this.showMutationError(err, 'delete');
      },
    });
  }

  private loadTriggers(): void {
    this.loading.set(true);
    this.error.set(null);
    // ``enabledOnly: false`` returns ALL triggers (enabled and disabled)
    // — the trigger list has an enable/disable toggle so filtering
    // disabled ones out server-side would make them disappear from
    // the UI as soon as the user flips them off.
    this.skillService.listTriggers(undefined, false).subscribe({
      next: (triggers) => {
        this.triggers.set(triggers ?? []);
        this.loading.set(false);
      },
      error: (err: Error) => {
        console.error('Failed to load skill triggers:', err);
        this.error.set(err?.message || 'Failed to load skill triggers');
        this.loading.set(false);
      },
    });
  }

  /**
   * Map an HTTP error onto a friendly snackbar message. Mirrors the
   * bank-page convention: 422 (validation) and 503 (write-paused)
   * surface friendly copy, anything else falls back to the raw error
   * message so unexpected failures are still diagnosable.
   */
  private showMutationError(
    err: Error & { status?: number },
    action: string,
  ): void {
    const status = err?.status;
    let message = err?.message || `Failed to ${action} trigger`;
    if (status === 422) {
      message = 'Trigger payload is invalid.';
    } else if (status === 503) {
      message = 'Service is temporarily paused for writes. Please try again later.';
    }
    this.snackBar.open(message, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar',
    });
  }
}