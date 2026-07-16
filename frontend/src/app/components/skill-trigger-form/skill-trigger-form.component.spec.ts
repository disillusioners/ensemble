import { Component, EventEmitter } from '@angular/core';
import { FormBuilder, FormGroup, Validators, AbstractControl } from '@angular/forms';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import type {
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

// ===========================================================================
// Testable mirror of `SkillTriggerFormComponent`.
//
// The codebase's established pattern (mcp-server-dialog, instance-delete-dialog,
// mcp-server-list) avoids Angular's TestBed/MatDialog interaction because
// MatDialog's internal DI causes flakes in JSDOM. We mirror the production
// logic here so the form's behaviour can be asserted end-to-end without
// fighting Angular Material's overlay stack.
//
// If the real component changes, this mirror must be updated to match.
// ===========================================================================

// ---- Stub dialog ref ----

class MockMatDialogRef {
  closeSpy = jest.fn();

  close(result?: unknown): void {
    this.closeSpy(result);
  }
}

// ---- Testable component ----

@Component({
  selector: 'app-skill-trigger-form-testable',
  standalone: true,
  template: '',
})
class TestableSkillTriggerFormComponent {
  protected readonly dialogRef: MockMatDialogRef;
  protected readonly data: { trigger?: SkillTrigger } | null;
  private readonly destroyRef = new Subject<void>();
  private readonly fb: FormBuilder;

  protected readonly form: FormGroup;

  protected get isEditMode(): boolean {
    return !!this.data?.trigger;
  }

  protected get conditionType(): ConditionType {
    return (this.form.get('condition_type')?.value as ConditionType) ?? 'low_completion_rate';
  }

  protected get showsThreshold(): boolean {
    return (
      this.conditionType === 'low_completion_rate' ||
      this.conditionType === 'high_fallback_rate' ||
      this.conditionType === 'consecutive_failures' ||
      this.conditionType === 'task_count_scan'
    );
  }

  protected get showsMinSelections(): boolean {
    return (
      this.conditionType === 'low_completion_rate' ||
      this.conditionType === 'high_fallback_rate'
    );
  }

  protected get showsIntervalDays(): boolean {
    return this.conditionType === 'periodic_scan';
  }

  protected get thresholdStep(): number {
    return this.conditionType === 'low_completion_rate' ||
      this.conditionType === 'high_fallback_rate'
      ? 0.1
      : 1;
  }

  protected get thresholdMin(): number {
    return this.conditionType === 'low_completion_rate' ||
      this.conditionType === 'high_fallback_rate'
      ? 0
      : 1;
  }

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

  constructor(dialogRef: MockMatDialogRef, data?: { trigger?: SkillTrigger } | null) {
    this.dialogRef = dialogRef;
    this.data = data ?? null;
    this.fb = new FormBuilder();

    this.form = this.fb.group({
      name: ['', [Validators.required, Validators.maxLength(120)]],
      condition_type: ['low_completion_rate' as ConditionType, Validators.required],
      threshold: [0.3 as number | null, [Validators.required, Validators.min(0)]],
      min_selections: [5 as number | null, [Validators.required, Validators.min(1)]],
      interval_days: [7 as number | null, [Validators.required, Validators.min(1)]],
      action: ['analyze' as string, Validators.required],
      is_enabled: [true],
    });
  }

  /** Equivalent of ngOnInit — call once after construction. */
  initialize(): void {
    if (this.isEditMode && this.data?.trigger) {
      this.prefillFromTrigger(this.data.trigger);
    } else {
      this.applyDefaultsForConditionType(this.conditionType);
    }

    this.form
      .get('condition_type')
      ?.valueChanges.pipe(takeUntil(this.destroyRef))
      .subscribe((newType) => {
        if (!newType) return;
        this.applyDefaultsForConditionType(newType as ConditionType);
      });
  }

  /** Tear down (test helper). */
  destroy(): void {
    this.destroyRef.next();
    this.destroyRef.complete();
  }

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
      threshold:
        pickNumber(
          json['threshold'],
          this.defaultFor(trigger.condition_type as ConditionType, 'threshold'),
        ) ?? fallback,
      min_selections:
        pickNumber(
          json['min_selections'],
          this.defaultFor(trigger.condition_type as ConditionType, 'min_selections'),
        ) ?? fallback,
      interval_days:
        pickNumber(
          json['interval_days'],
          this.defaultFor(trigger.condition_type as ConditionType, 'interval_days'),
        ) ?? fallback,
    });
  }

  private applyDefaultsForConditionType(type: ConditionType): void {
    const defaults = CONDITION_TYPE_DEFAULTS[type] ?? {};
    // Reset every dynamic control to the type's default, OR a sentinel
    // fallback so Validators.required is satisfied even when the field
    // is hidden by the active condition_type. The template hides the
    // input via `@if`, so a non-null sentinel here is harmless.
    const fallback = 1;
    this.form.patchValue({
      threshold: typeof defaults['threshold'] === 'number' ? defaults['threshold'] : fallback,
      min_selections:
        typeof defaults['min_selections'] === 'number' ? defaults['min_selections'] : fallback,
      interval_days:
        typeof defaults['interval_days'] === 'number' ? defaults['interval_days'] : fallback,
    });
  }

  private defaultFor(type: ConditionType, key: string): number | null {
    const value = CONDITION_TYPE_DEFAULTS[type]?.[key];
    return typeof value === 'number' ? value : null;
  }

  handleClose(): void {
    this.dialogRef.close();
  }

  handleSubmit(): void {
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

  isSubmitDisabled(): boolean {
    return this.form.invalid;
  }
}

// ===========================================================================
// Factory helpers
// ===========================================================================

function createMockTrigger(overrides: Partial<SkillTrigger> = {}): SkillTrigger {
  return {
    id: 'trigger-1',
    project_id: null,
    name: 'Existing Trigger',
    condition_type: 'low_completion_rate',
    condition_json: { threshold: 0.4, min_selections: 10 },
    action: 'analyze',
    is_enabled: true,
    created_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function createComponent(data?: { trigger?: SkillTrigger } | null): {
  component: TestableSkillTriggerFormComponent;
  dialogRef: MockMatDialogRef;
} {
  const dialogRef = new MockMatDialogRef();
  const component = new TestableSkillTriggerFormComponent(dialogRef, data);
  component.initialize();
  return { component, dialogRef };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('SkillTriggerFormComponent', () => {
  describe('create mode', () => {
    let component: TestableSkillTriggerFormComponent;
    let dialogRef: MockMatDialogRef;

    beforeEach(() => {
      ({ component, dialogRef } = createComponent());
    });

    afterEach(() => {
      component.destroy();
    });

    // ---- 1. Component creates ----

    it('should create successfully in create mode', () => {
      expect(component).toBeTruthy();
    });

    it('should default condition_type to low_completion_rate', () => {
      expect(component.form.get('condition_type')?.value).toBe('low_completion_rate');
    });

    it('should default action to analyze', () => {
      expect(component.form.get('action')?.value).toBe('analyze');
    });

    it('should default is_enabled to true', () => {
      expect(component.form.get('is_enabled')?.value).toBe(true);
    });

    it('should not be in edit mode when no data is provided', () => {
      expect(component.isEditMode).toBe(false);
    });

    it('should populate default threshold (0.3) and min_selections (5) for low_completion_rate', () => {
      expect(component.form.get('threshold')?.value).toBe(0.3);
      expect(component.form.get('min_selections')?.value).toBe(5);
    });

    it('should expose threshold and min_selections dynamic fields for low_completion_rate', () => {
      expect(component.showsThreshold).toBe(true);
      expect(component.showsMinSelections).toBe(true);
      expect(component.showsIntervalDays).toBe(false);
    });

    // ---- 3. Dynamic field toggling ----

    it('should hide threshold and min_selections and show interval_days when periodic_scan is selected', () => {
      component.form.get('condition_type')?.setValue('periodic_scan');

      expect(component.showsThreshold).toBe(false);
      expect(component.showsMinSelections).toBe(false);
      expect(component.showsIntervalDays).toBe(true);
      // Default interval_days populated
      expect(component.form.get('interval_days')?.value).toBe(7);
    });

    it('should show threshold but hide min_selections and interval_days for consecutive_failures', () => {
      component.form.get('condition_type')?.setValue('consecutive_failures');

      expect(component.showsThreshold).toBe(true);
      expect(component.showsMinSelections).toBe(false);
      expect(component.showsIntervalDays).toBe(false);
      // Default threshold populated for consecutive_failures
      expect(component.form.get('threshold')?.value).toBe(3);
    });

    it('should show threshold and min_selections for high_fallback_rate', () => {
      component.form.get('condition_type')?.setValue('high_fallback_rate');

      expect(component.showsThreshold).toBe(true);
      expect(component.showsMinSelections).toBe(true);
      expect(component.showsIntervalDays).toBe(false);
      // Default threshold for high_fallback_rate
      expect(component.form.get('threshold')?.value).toBe(0.5);
    });

    it('should show threshold but hide min_selections and interval_days for task_count_scan', () => {
      component.form.get('condition_type')?.setValue('task_count_scan');

      expect(component.showsThreshold).toBe(true);
      expect(component.showsMinSelections).toBe(false);
      expect(component.showsIntervalDays).toBe(false);
      expect(component.form.get('threshold')?.value).toBe(20);
    });

    // ---- 4. Save disabled when name empty ----

    it('should disable save when name is empty', () => {
      expect(component.form.get('name')?.value).toBe('');
      expect(component.isSubmitDisabled()).toBe(true);
    });

    it('should enable save when all required fields are filled', () => {
      component.form.patchValue({
        name: 'My Trigger',
        condition_type: 'low_completion_rate',
        threshold: 0.4,
        min_selections: 10,
      });
      expect(component.isSubmitDisabled()).toBe(false);
    });

    // ---- 5. Save emits SkillTriggerCreate ----

    it('should close with a SkillTriggerCreate payload on submit in create mode', () => {
      component.form.patchValue({
        name: 'New Trigger',
        condition_type: 'low_completion_rate',
        threshold: 0.4,
        min_selections: 7,
        action: 'evolve_fix',
        is_enabled: true,
      });

      component.handleSubmit();

      expect(dialogRef.closeSpy).toHaveBeenCalledTimes(1);
      const payload = dialogRef.closeSpy.mock.calls[0][0] as SkillTriggerCreate;
      expect(payload).toBeDefined();
      expect(payload.name).toBe('New Trigger');
      expect(payload.condition_type).toBe('low_completion_rate');
      expect(payload.condition_json).toEqual({ threshold: 0.4, min_selections: 7 });
      expect(payload.action).toBe('evolve_fix');
      expect(payload.is_enabled).toBe(true);
    });

    it('should NOT close the dialog when the form is invalid', () => {
      // Name is empty → form invalid
      component.handleSubmit();
      expect(dialogRef.closeSpy).not.toHaveBeenCalled();
    });

    it('should build correct condition_json for periodic_scan (interval_days only)', () => {
      component.form.patchValue({
        name: 'Periodic Trigger',
        condition_type: 'periodic_scan',
        interval_days: 14,
      });

      component.handleSubmit();

      const payload = dialogRef.closeSpy.mock.calls[0][0] as SkillTriggerCreate;
      expect(payload.condition_json).toEqual({ interval_days: 14 });
    });

    it('should build correct condition_json for consecutive_failures (threshold only)', () => {
      component.form.patchValue({
        name: 'Failures',
        condition_type: 'consecutive_failures',
        threshold: 5,
      });

      component.handleSubmit();

      const payload = dialogRef.closeSpy.mock.calls[0][0] as SkillTriggerCreate;
      expect(payload.condition_json).toEqual({ threshold: 5 });
    });

    it('should close without a value when the cancel button is clicked', () => {
      component.handleClose();
      // closeSpy is called with `undefined` when no argument is supplied,
      // which is the dialog-cancelled signal the parent receives.
      expect(dialogRef.closeSpy).toHaveBeenCalledWith(undefined);
    });
  });

  // ---- 2 & 6. Edit mode ----

  describe('edit mode', () => {
    let component: TestableSkillTriggerFormComponent;
    let dialogRef: MockMatDialogRef;

    beforeEach(() => {
      const mockTrigger = createMockTrigger();
      ({ component, dialogRef } = createComponent({ trigger: mockTrigger }));
    });

    afterEach(() => {
      component.destroy();
    });

    it('should create successfully in edit mode', () => {
      expect(component).toBeTruthy();
    });

    it('should be in edit mode when data.trigger is provided', () => {
      expect(component.isEditMode).toBe(true);
    });

    it('should pre-fill name from the trigger data', () => {
      expect(component.form.get('name')?.value).toBe('Existing Trigger');
    });

    it('should pre-fill condition_type from the trigger data', () => {
      expect(component.form.get('condition_type')?.value).toBe('low_completion_rate');
    });

    it('should pre-fill threshold from condition_json', () => {
      expect(component.form.get('threshold')?.value).toBe(0.4);
    });

    it('should pre-fill min_selections from condition_json', () => {
      expect(component.form.get('min_selections')?.value).toBe(10);
    });

    it('should pre-fill action from the trigger data', () => {
      expect(component.form.get('action')?.value).toBe('analyze');
    });

    it('should pre-fill is_enabled from the trigger data', () => {
      expect(component.form.get('is_enabled')?.value).toBe(true);
    });

    it('should close with a SkillTriggerUpdate payload on submit', () => {
      component.form.patchValue({
        name: 'Renamed',
        is_enabled: false,
        threshold: 0.5,
        min_selections: 8,
        action: 'evolve_fix',
      });

      component.handleSubmit();

      expect(dialogRef.closeSpy).toHaveBeenCalledTimes(1);
      const payload = dialogRef.closeSpy.mock.calls[0][0] as SkillTriggerUpdate;
      expect(payload).toBeDefined();
      expect(payload.name).toBe('Renamed');
      expect(payload.condition_type).toBe('low_completion_rate');
      expect(payload.condition_json).toEqual({ threshold: 0.5, min_selections: 8 });
      expect(payload.action).toBe('evolve_fix');
      expect(payload.is_enabled).toBe(false);
    });

    it('should pre-fill dynamic fields correctly for periodic_scan triggers', () => {
      component.destroy();

      const periodicTrigger = createMockTrigger({
        condition_type: 'periodic_scan',
        condition_json: { interval_days: 30 },
      });
      ({ component, dialogRef } = createComponent({ trigger: periodicTrigger }));

      expect(component.form.get('condition_type')?.value).toBe('periodic_scan');
      expect(component.form.get('interval_days')?.value).toBe(30);
      // Threshold and min_selections are not displayed when condition_type
      // is periodic_scan (they're hidden via `@if` in the template), but
      // the controls still hold a non-null sentinel value so
      // Validators.required is satisfied.
      expect(component.form.get('threshold')?.value).toBe(1);
      expect(component.form.get('min_selections')?.value).toBe(1);
    });

    it('should fall back to type defaults when condition_json is missing keys', () => {
      component.destroy();

      const sparseTrigger = createMockTrigger({
        condition_type: 'low_completion_rate',
        // Only threshold provided, min_selections missing
        condition_json: { threshold: 0.5 },
      });
      ({ component, dialogRef } = createComponent({ trigger: sparseTrigger }));

      // Provided threshold respected.
      expect(component.form.get('threshold')?.value).toBe(0.5);
      // Missing key falls back to the type default (5).
      expect(component.form.get('min_selections')?.value).toBe(5);
    });
  });
});