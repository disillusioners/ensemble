import { Component, inject, OnInit, DestroyRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators, AbstractControl } from '@angular/forms';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatIconModule } from '@angular/material/icon';

import {
  SkillTrigger,
  SkillTriggerCreate,
  SkillTriggerUpdate,
} from '../../models/skill.model';

import {
  buildConditionJson,
  CONDITION_TYPE_DEFAULTS,
  ConditionType,
  pickNumber,
} from './form-helpers';

/**
 * Dialog data passed in via `MAT_DIALOG_DATA`. The presence of `trigger`
 * is what switches the dialog into edit mode — absence implies create.
 */
export interface SkillTriggerFormDialogData {
  trigger?: SkillTrigger;
}

/**
 * Human-readable labels for the condition-type `<mat-select>`. Exported
 * so the list component (and any future shared selector) can render
 * the same canonical strings without having to redeclare the mapping.
 */
export const CONDITION_TYPE_OPTIONS: ReadonlyArray<{ value: ConditionType; label: string }> = [
  { value: 'low_completion_rate', label: 'Low Completion Rate' },
  { value: 'high_fallback_rate', label: 'High Fallback Rate' },
  { value: 'consecutive_failures', label: 'Consecutive Failures' },
  { value: 'task_count_scan', label: 'Task Count Scan' },
  { value: 'periodic_scan', label: 'Periodic Scan' },
];

const ACTION_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'analyze', label: 'Analyze' },
  { value: 'evolve_fix', label: 'Evolve (Fix)' },
];

/**
 * Material dialog for creating or editing a skill evolution trigger.
 *
 * Mirrors the queue-create-dialog convention: ReactiveForms + custom
 * modal markup (NOT MatDialog structural directives). On save the
 * dialog closes itself with the built payload:
 *  - create mode → :class:`SkillTriggerCreate`
 *  - edit mode → :class:`SkillTriggerUpdate`
 *
 * Dynamic config fields are rebuilt when `condition_type` changes so
 * each type only shows the inputs that make sense for it.
 */
@Component({
  selector: 'app-skill-trigger-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatIconModule,
  ],
  templateUrl: './skill-trigger-form.component.html',
  styleUrl: './skill-trigger-form.component.scss',
})
export class SkillTriggerFormComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly dialogRef = inject<MatDialogRef<SkillTriggerFormComponent>>(MatDialogRef);
  protected readonly data = inject<SkillTriggerFormDialogData>(MAT_DIALOG_DATA, { optional: true });

  /** True when the dialog was opened with an existing trigger to edit. */
  protected readonly isEditMode = !!this.data?.trigger;

  protected readonly conditionTypeOptions = CONDITION_TYPE_OPTIONS;
  protected readonly actionOptions = ACTION_OPTIONS;

  /**
   * Reactive form. Note that dynamic `threshold` / `min_selections` /
   * `interval_days` controls are always present — they are toggled
   * enabled/disabled and re-defaulted by the condition_type handler so
   * the form layout never needs to remount controls mid-edit.
   */
  protected readonly form: FormGroup = this.fb.group({
    name: ['', [Validators.required, Validators.maxLength(120)]],
    condition_type: ['low_completion_rate' as ConditionType, Validators.required],
    // Dynamic fields — defaults populated in ngOnInit / on condition_type change.
    threshold: [0.3 as number | null, [Validators.required, Validators.min(0)]],
    min_selections: [5 as number | null, [Validators.required, Validators.min(1)]],
    interval_days: [7 as number | null, [Validators.required, Validators.min(1)]],
    action: ['analyze' as string, Validators.required],
    is_enabled: [true],
  });

  ngOnInit(): void {
    // Seed dynamic fields based on initial condition_type (or edit-mode data).
    if (this.isEditMode && this.data?.trigger) {
      this.prefillFromTrigger(this.data.trigger);
    } else {
      this.applyDefaultsForConditionType(this.conditionType);
    }

    // React to condition_type changes — rebuild dynamic defaults and
    // re-validate. The form layout stays put, but values are replaced.
    this.form
      .get('condition_type')
      ?.valueChanges.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((newType) => {
        if (!newType) return;
        this.applyDefaultsForConditionType(newType as ConditionType);
      });
  }

  /** Pre-fill the form when editing an existing trigger. */
  private prefillFromTrigger(trigger: SkillTrigger): void {
    const json = trigger.condition_json ?? {};
    // Sentinel fallback ensures `Validators.required` is satisfied even
    // when the field is hidden by the active condition_type. The form
    // layout hides the input via `@if`, so the value is harmless.
    const fallback = 1;
    this.form.patchValue({
      name: trigger.name,
      condition_type: trigger.condition_type as ConditionType,
      action: trigger.action,
      is_enabled: trigger.is_enabled,
      // Numeric fields — fall back to type defaults if missing.
      threshold: pickNumber(json['threshold'], this.defaultFor(trigger.condition_type as ConditionType, 'threshold')) ?? fallback,
      min_selections: pickNumber(
        json['min_selections'],
        this.defaultFor(trigger.condition_type as ConditionType, 'min_selections'),
      ) ?? fallback,
      interval_days: pickNumber(
        json['interval_days'],
        this.defaultFor(trigger.condition_type as ConditionType, 'interval_days'),
      ) ?? fallback,
    });
  }

  /** Apply the default numeric values for the active condition_type. */
  private applyDefaultsForConditionType(type: ConditionType): void {
    const defaults = CONDITION_TYPE_DEFAULTS[type] ?? {};
    // Reset every dynamic control to the type's default, OR a sentinel
    // fallback so Validators.required is satisfied even when the field
    // is hidden by the active condition_type. The template hides the
    // input via `@if`, so a non-null sentinel here is harmless.
    const fallback = 1;
    this.form.patchValue({
      threshold:
        typeof defaults['threshold'] === 'number' ? defaults['threshold'] : fallback,
      min_selections:
        typeof defaults['min_selections'] === 'number' ? defaults['min_selections'] : fallback,
      interval_days:
        typeof defaults['interval_days'] === 'number' ? defaults['interval_days'] : fallback,
    });
  }

  /** Look up a default value for a dynamic control, or `null` if undefined. */
  private defaultFor(type: ConditionType, key: string): number | null {
    const value = CONDITION_TYPE_DEFAULTS[type]?.[key];
    return typeof value === 'number' ? value : null;
  }

  // ── Template helpers ─────────────────────────────────────────────

  protected get conditionType(): ConditionType {
    return (this.form.get('condition_type')?.value as ConditionType) ?? 'low_completion_rate';
  }

  /** True when the active condition_type uses a `threshold` field. */
  protected get showsThreshold(): boolean {
    return (
      this.conditionType === 'low_completion_rate' ||
      this.conditionType === 'high_fallback_rate' ||
      this.conditionType === 'consecutive_failures' ||
      this.conditionType === 'task_count_scan'
    );
  }

  /** True when the active condition_type uses a `min_selections` field. */
  protected get showsMinSelections(): boolean {
    return (
      this.conditionType === 'low_completion_rate' ||
      this.conditionType === 'high_fallback_rate'
    );
  }

  /** True when the active condition_type uses an `interval_days` field. */
  protected get showsIntervalDays(): boolean {
    return this.conditionType === 'periodic_scan';
  }

  /** Threshold step / min — clamped per condition_type for nicer UX. */
  protected get thresholdStep(): number {
    return this.conditionType === 'low_completion_rate' || this.conditionType === 'high_fallback_rate'
      ? 0.1
      : 1;
  }

  protected get thresholdMin(): number {
    return this.conditionType === 'low_completion_rate' || this.conditionType === 'high_fallback_rate'
      ? 0
      : 1;
  }

  // ── Actions ──────────────────────────────────────────────────────

  protected handleClose(): void {
    this.dialogRef.close();
  }

  protected handleSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const raw = this.form.getRawValue();
    const conditionType = raw['condition_type'] as ConditionType;
    const conditionJson = buildConditionJson(conditionType, raw);
    const name = (raw['name'] as string).trim();

    if (this.isEditMode) {
      const update: SkillTriggerUpdate = {
        name,
        condition_type: conditionType,
        condition_json: conditionJson,
        action: raw['action'] as string,
        is_enabled: raw['is_enabled'] as boolean,
      };
      this.dialogRef.close(update);
    } else {
      const create: SkillTriggerCreate = {
        name,
        condition_type: conditionType,
        condition_json: conditionJson,
        action: raw['action'] as string,
        is_enabled: raw['is_enabled'] as boolean,
      };
      this.dialogRef.close(create);
    }
  }

  protected isSubmitDisabled(): boolean {
    return this.form.invalid;
  }

  // ── Form control accessors for the template ──────────────────────

  protected get nameControl(): AbstractControl | null {
    return this.form.get('name');
  }

  protected get thresholdControl(): AbstractControl | null {
    return this.form.get('threshold');
  }

  protected get minSelectionsControl(): AbstractControl | null {
    return this.form.get('min_selections');
  }

  protected get intervalDaysControl(): AbstractControl | null {
    return this.form.get('interval_days');
  }
}