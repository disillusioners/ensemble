import { Component, input, output, inject, Inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogModule, MatDialogRef, MAT_DIALOG_DATA } from '@angular/material/dialog';

import {
  SkillTrigger,
  SkillTriggerCreate,
  SkillTriggerUpdate,
} from '../../models/skill.model';
import { SkillTriggerFormComponent, SkillTriggerFormDialogData } from '../skill-trigger-form/skill-trigger-form.component';

/**
 * Human-readable labels for the five built-in condition types.
 *
 * Kept module-scoped (not on the component) so it is independently
 * importable for tests and so the form dialog can reuse the same
 * canonical mapping without depending on this component instance.
 */
export const CONDITION_TYPE_LABELS: Record<string, string> = {
  low_completion_rate: 'Low Completion Rate',
  high_fallback_rate: 'High Fallback Rate',
  consecutive_failures: 'Consecutive Failures',
  task_count_scan: 'Task Count Scan',
  periodic_scan: 'Periodic Scan',
};

/**
 * Inline confirmation dialog opened from the list when the user asks
 * to delete a trigger. Defined here (and NOT as a separate component
 * file) so this feature ships a single, self-contained list surface
 * — Phase 6 integration can promote it to a shared confirm dialog
 * if more delete-confirmation callers appear.
 */
@Component({
  selector: 'app-skill-trigger-confirm-dialog',
  standalone: true,
  imports: [CommonModule, MatDialogModule, MatButtonModule],
  template: `
    <div class="confirm-dialog">
      <h2 mat-dialog-title>Delete Trigger</h2>
      <mat-dialog-content>
        <p class="warning-message">
          Are you sure you want to delete
          <strong>{{ triggerName }}</strong>?
        </p>
        <p class="consequence">This action cannot be undone.</p>
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button (click)="cancel()">Cancel</button>
        <button mat-flat-button color="warn" (click)="confirm()">Delete</button>
      </mat-dialog-actions>
    </div>
  `,
  styles: [
    `
      .confirm-dialog .warning-message {
        margin: 0 0 8px 0;
        color: rgba(0, 0, 0, 0.85);
      }
      .confirm-dialog .consequence {
        margin: 0;
        font-size: 13px;
        color: rgba(0, 0, 0, 0.6);
      }
    `,
  ],
})
export class SkillTriggerConfirmDialogComponent {
  protected readonly dialogRef =
    inject<MatDialogRef<SkillTriggerConfirmDialogComponent>>(MatDialogRef);

  constructor(@Inject(MAT_DIALOG_DATA) protected readonly data: { triggerName: string }) {}

  protected get triggerName(): string {
    return this.data?.triggerName ?? '';
  }

  protected cancel(): void {
    this.dialogRef.close(false);
  }

  protected confirm(): void {
    this.dialogRef.close(true);
  }
}

/**
 * Presentational list of skill evolution triggers.
 *
 * Renders each trigger as a Material card with its config summary,
 * an enable/disable slide-toggle, and edit/delete buttons. The
 * parent (Phase 6) wires the actual SkillService calls; this
 * component is event-driven only — see the three `output()`s.
 *
 * Following the project-delete-dialog convention, the delete
 * confirmation opens an inline Material dialog
 * (:class:`SkillTriggerConfirmDialogComponent`) instead of using
 * `window.confirm()` so the experience is consistent with the rest
 * of the app and matches the queue-list / skills-page conventions.
 */
@Component({
  selector: 'app-skill-trigger-list',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatSlideToggleModule,
    MatTooltipModule,
    MatDialogModule,
  ],
  templateUrl: './skill-trigger-list.component.html',
  styleUrl: './skill-trigger-list.component.scss',
})
export class SkillTriggerListComponent {
  private readonly dialog = inject(MatDialog);

  // ── Inputs ────────────────────────────────────────────────────────

  /** Triggers to render. Parent owns data fetching — this is presentational. */
  triggers = input<SkillTrigger[]>([]);

  // ── Outputs ───────────────────────────────────────────────────────

  create = output<SkillTriggerCreate>();
  update = output<{ id: string; data: SkillTriggerUpdate }>();
  delete = output<string>();

  // ── Display helpers ───────────────────────────────────────────────

  /** Human label for a condition_type value, falling back to the raw string. */
  protected conditionTypeLabel(type: string): string {
    return CONDITION_TYPE_LABELS[type] ?? type;
  }

  /**
   * Format `condition_json` as readable "Key: Value, Key: Value" pairs
   * so the card does not render a raw JSON dump. Falls back to the
   * raw JSON for unknown shapes so the user can still inspect the
   * data.
   */
  protected formatConfigSummary(config: Record<string, unknown> | null | undefined): string {
    if (!config || Object.keys(config).length === 0) {
      return '(no config)';
    }
    const parts: string[] = [];
    for (const [key, value] of Object.entries(config)) {
      if (value === null || value === undefined) continue;
      // Pretty-print key: human_label(key) + value
      parts.push(`${this.configKeyLabel(key)}: ${this.formatConfigValue(value)}`);
    }
    return parts.length > 0 ? parts.join(', ') : JSON.stringify(config);
  }

  /** Pretty-print a single config value for the summary line. */
  private formatConfigValue(value: unknown): string {
    if (typeof value === 'number') {
      // Render integer-like numbers without decimals for readability.
      return Number.isInteger(value) ? value.toString() : value.toString();
    }
    if (typeof value === 'boolean') {
      return value ? 'yes' : 'no';
    }
    if (Array.isArray(value)) {
      return value.map((v) => this.formatConfigValue(v)).join(', ');
    }
    if (typeof value === 'object') {
      return JSON.stringify(value);
    }
    return String(value);
  }

  /** Convert snake_case config keys to a more readable label. */
  private configKeyLabel(key: string): string {
    return key
      .split('_')
      .map((segment) => (segment.length > 0 ? segment[0].toUpperCase() + segment.slice(1) : ''))
      .join(' ');
  }

  /** Action as a small badge label. */
  protected actionLabel(action: string): string {
    if (!action) return '';
    return action
      .split('_')
      .map((s) => (s.length > 0 ? s[0].toUpperCase() + s.slice(1) : s))
      .join(' ');
  }

  // ── Toggle ────────────────────────────────────────────────────────

  /**
   * Slide-toggle changed — emit a partial update that flips only the
   * `is_enabled` flag, leaving the rest of the trigger untouched. The
   * parent is responsible for round-tripping the original trigger.
   */
  protected onToggle(trigger: SkillTrigger, checked: boolean): void {
    if (trigger.is_enabled === checked) return;
    this.update.emit({
      id: trigger.id,
      data: { is_enabled: checked },
    });
  }

  // ── Dialog flow ───────────────────────────────────────────────────

  protected openCreateDialog(): void {
    const ref = this.dialog.open<
      SkillTriggerFormComponent,
      SkillTriggerFormDialogData,
      SkillTriggerCreate | undefined
    >(SkillTriggerFormComponent, {
      width: '520px',
      panelClass: 'dark-modal-panel',
    });

    ref.afterClosed().subscribe((result) => {
      if (result) {
        this.create.emit(result);
      }
    });
  }

  protected openEditDialog(trigger: SkillTrigger): void {
    const ref = this.dialog.open<
      SkillTriggerFormComponent,
      SkillTriggerFormDialogData,
      SkillTriggerUpdate | undefined
    >(SkillTriggerFormComponent, {
      width: '520px',
      panelClass: 'dark-modal-panel',
      data: { trigger },
    });

    ref.afterClosed().subscribe((result) => {
      if (result) {
        this.update.emit({ id: trigger.id, data: result });
      }
    });
  }

  /**
   * Open the inline confirm dialog. We deliberately do not collapse
   * the spinner / disable button on confirm because the parent owns
   * the actual delete call and will emit a refreshed `triggers()`
   * after success.
   */
  protected onDelete(trigger: SkillTrigger): void {
    const ref = this.dialog.open<
      SkillTriggerConfirmDialogComponent,
      { triggerName: string },
      boolean
    >(SkillTriggerConfirmDialogComponent, {
      width: '420px',
      panelClass: 'dark-modal-panel',
      data: { triggerName: trigger.name },
    });

    ref.afterClosed().subscribe((confirmed) => {
      if (confirmed) {
        this.delete.emit(trigger.id);
      }
    });
  }

  // ── trackBy ───────────────────────────────────────────────────────

  protected trackById(_index: number, trigger: SkillTrigger): string {
    return trigger.id;
  }
}