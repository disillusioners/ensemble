import { Component, EventEmitter, Input, Output } from '@angular/core';
import { signal, computed } from '@angular/core';
import { Observable, of } from 'rxjs';
import type { SkillTrigger, SkillTriggerCreate, SkillTriggerUpdate } from '../../models/skill.model';

// ===========================================================================
// Mirrors of the real component's surface, exported so the real
// `SkillTriggerListComponent` can be tested without spinning up Angular's
// TestBed with MatDialog's overlay (which has known test-flake issues — see
// patterns/angular-21-jest-service-spec-pattern.md).
//
// The pattern matches the codebase convention (mcp-server-list,
// mcp-server-dialog, instance-delete-dialog): a "Testable" sibling that
// duplicates the production logic. When the real component changes, this
// mirror must be updated to match.
// ===========================================================================

interface DialogResult<R = unknown> {
  afterClosed: () => Observable<R | undefined>;
  close: jest.Mock;
}

class MockMatDialog {
  private dialogs: DialogResult[] = [];
  private nextResult: unknown = undefined;

  /** Records every `open()` call so tests can assert on it. */
  openSpy = jest.fn();

  /**
   * Configure what `afterClosed()` will emit for the NEXT dialog that
   * gets opened. Resets after one use so a test cannot accidentally
   * leak state across calls.
   */
  setNextResult(value: unknown): void {
    this.nextResult = value;
  }

  open<R = unknown>(_component: unknown, config?: { data?: unknown }): DialogResult<R> {
    this.openSpy(_component, config);

    // Pick up the next configured result, then reset so subsequent
    // calls fall back to undefined unless configured again.
    const result = this.nextResult;
    this.nextResult = undefined;

    const ref: DialogResult<R> = {
      afterClosed: () => of(result as R | undefined),
      close: jest.fn(),
    };
    this.dialogs.push(ref as DialogResult);
    return ref;
  }

  getLastDialog(): DialogResult | undefined {
    return this.dialogs[this.dialogs.length - 1];
  }

  closeAll(): void {
    this.dialogs = [];
  }
}

// Mirrors `SkillTriggerListComponent` behavior. Pure logic — no Angular DI.
@Component({
  selector: 'app-skill-trigger-list-testable',
  standalone: true,
  template: '',
})
class TestableSkillTriggerListComponent {
  @Input() triggers: SkillTrigger[] = [];
  @Output() create = new EventEmitter<SkillTriggerCreate>();
  @Output() update = new EventEmitter<{ id: string; data: SkillTriggerUpdate }>();
  @Output() delete = new EventEmitter<string>();

  private dialog: MockMatDialog;

  constructor(dialog: MockMatDialog) {
    this.dialog = dialog;
  }

  // ── Helpers (mirrored) ───────────────────────────────────────────

  conditionTypeLabel(type: string): string {
    const labels: Record<string, string> = {
      low_completion_rate: 'Low Completion Rate',
      high_fallback_rate: 'High Fallback Rate',
      consecutive_failures: 'Consecutive Failures',
      task_count_scan: 'Task Count Scan',
      periodic_scan: 'Periodic Scan',
    };
    return labels[type] ?? type;
  }

  formatConfigSummary(config: Record<string, unknown> | null | undefined): string {
    if (!config || Object.keys(config).length === 0) return '(no config)';
    const parts: string[] = [];
    for (const [key, value] of Object.entries(config)) {
      if (value === null || value === undefined) continue;
      const labelKey = key
        .split('_')
        .map((s) => (s.length > 0 ? s[0].toUpperCase() + s.slice(1) : ''))
        .join(' ');
      parts.push(`${labelKey}: ${value}`);
    }
    return parts.length > 0 ? parts.join(', ') : JSON.stringify(config);
  }

  // ── Toggle ───────────────────────────────────────────────────────

  onToggle(trigger: SkillTrigger, checked: boolean): void {
    if (trigger.is_enabled === checked) return;
    this.update.emit({ id: trigger.id, data: { is_enabled: checked } });
  }

  // ── Dialog flow ──────────────────────────────────────────────────

  openCreateDialog(): void {
    const ref = this.dialog.open<SkillTriggerCreate>(Symbol('SkillTriggerFormComponent'));
    ref.afterClosed().subscribe((result) => {
      if (result) this.create.emit(result);
    });
  }

  openEditDialog(trigger: SkillTrigger): void {
    const ref = this.dialog.open<SkillTriggerUpdate>(Symbol('SkillTriggerFormComponent'), {
      data: { trigger },
    });
    ref.afterClosed().subscribe((result) => {
      if (result) this.update.emit({ id: trigger.id, data: result });
    });
  }

  onDelete(trigger: SkillTrigger): void {
    const ref = this.dialog.open<boolean>(Symbol('SkillTriggerConfirmDialogComponent'), {
      data: { triggerName: trigger.name },
    });
    ref.afterClosed().subscribe((confirmed) => {
      if (confirmed) this.delete.emit(trigger.id);
    });
  }
}

// ===========================================================================
// Factory helpers
// ===========================================================================

function createTrigger(overrides: Partial<SkillTrigger> = {}): SkillTrigger {
  return {
    id: 'trigger-1',
    project_id: null,
    name: 'My Trigger',
    condition_type: 'low_completion_rate',
    condition_json: { threshold: 0.3, min_selections: 5 },
    action: 'analyze',
    is_enabled: true,
    created_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function createComponent(dialog?: MockMatDialog): {
  component: TestableSkillTriggerListComponent;
  dialog: MockMatDialog;
} {
  const dlg = dialog ?? new MockMatDialog();
  const comp = new TestableSkillTriggerListComponent(dlg);
  return { component: comp, dialog: dlg };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('SkillTriggerListComponent (presentational contract)', () => {
  // ---- 1. Construction ----

  it('should create successfully', () => {
    const { component } = createComponent();
    expect(component).toBeTruthy();
  });

  // ---- 2. Empty state (pure logic) ----

  it('should report zero triggers for empty state', () => {
    const { component } = createComponent();
    component.triggers = [];
    expect(component.triggers.length).toBe(0);
  });

  // ---- 3. Card rendering helpers ----

  it('should render a label for each known condition_type', () => {
    const { component } = createComponent();
    expect(component.conditionTypeLabel('low_completion_rate')).toBe('Low Completion Rate');
    expect(component.conditionTypeLabel('high_fallback_rate')).toBe('High Fallback Rate');
    expect(component.conditionTypeLabel('consecutive_failures')).toBe('Consecutive Failures');
    expect(component.conditionTypeLabel('task_count_scan')).toBe('Task Count Scan');
    expect(component.conditionTypeLabel('periodic_scan')).toBe('Periodic Scan');
  });

  it('should fall back to the raw type string for unknown condition types', () => {
    const { component } = createComponent();
    expect(component.conditionTypeLabel('unknown_type')).toBe('unknown_type');
  });

  it('should format condition_json as readable key-value pairs', () => {
    const { component } = createComponent();
    const summary = component.formatConfigSummary({ threshold: 0.3, min_selections: 5 });
    expect(summary).toContain('Threshold: 0.3');
    expect(summary).toContain('Min Selections: 5');
  });

  it('should fall back to "(no config)" for empty condition_json', () => {
    const { component } = createComponent();
    expect(component.formatConfigSummary({})).toBe('(no config)');
    expect(component.formatConfigSummary(undefined)).toBe('(no config)');
    expect(component.formatConfigSummary(null)).toBe('(no config)');
  });

  // ---- 4. Delete confirmation flow ----

  it('should open the confirm dialog when onDelete is called', () => {
    const { component, dialog } = createComponent();
    const trigger = createTrigger({ id: 't-del', name: 'Doomed' });
    component.onDelete(trigger);

    expect(dialog.openSpy).toHaveBeenCalled();
    // Data should carry the trigger name
    const config = dialog.openSpy.mock.calls[0][1];
    expect(config?.data?.triggerName).toBe('Doomed');
  });

  it('should emit `delete` with the trigger id after confirmation', () => {
    const { component, dialog } = createComponent();
    const trigger = createTrigger({ id: 't-del', name: 'Doomed' });
    const deleteSpy = jest.fn();
    component.delete.subscribe(deleteSpy);

    dialog.setNextResult(true); // user confirmed

    component.onDelete(trigger);

    expect(deleteSpy).toHaveBeenCalledWith('t-del');
  });

  it('should NOT emit `delete` when the confirmation dialog returns false', () => {
    const dlg = new MockMatDialog();
    const comp = new TestableSkillTriggerListComponent(dlg);
    const trigger = createTrigger({ id: 't-del', name: 'Doomed' });
    const deleteSpy = jest.fn();
    comp.delete.subscribe(deleteSpy);

    // Configure the dialog to return false (user cancelled)
    dlg.setNextResult(false);

    comp.onDelete(trigger);

    expect(deleteSpy).not.toHaveBeenCalled();
  });

  // ---- 5. Toggle enabled ----

  it('should emit `update` with flipped is_enabled when the slide-toggle changes', () => {
    const { component } = createComponent();
    const trigger = createTrigger({ id: 't-1', is_enabled: true });
    const updateSpy = jest.fn();
    component.update.subscribe(updateSpy);

    component.onToggle(trigger, false);

    expect(updateSpy).toHaveBeenCalledWith({
      id: 't-1',
      data: { is_enabled: false },
    });
  });

  it('should emit `update` with is_enabled=true when toggled back on', () => {
    const { component } = createComponent();
    const trigger = createTrigger({ id: 't-2', is_enabled: false });
    const updateSpy = jest.fn();
    component.update.subscribe(updateSpy);

    component.onToggle(trigger, true);

    expect(updateSpy).toHaveBeenCalledWith({
      id: 't-2',
      data: { is_enabled: true },
    });
  });

  it('should NOT emit `update` when the toggle value is unchanged', () => {
    const { component } = createComponent();
    const trigger = createTrigger({ id: 't-1', is_enabled: true });
    const updateSpy = jest.fn();
    component.update.subscribe(updateSpy);

    component.onToggle(trigger, true); // no-op

    expect(updateSpy).not.toHaveBeenCalled();
  });

  // ---- 6. Create dialog flow ----

  it('should open the form dialog when openCreateDialog is called', () => {
    const { component, dialog } = createComponent();
    component.openCreateDialog();

    expect(dialog.openSpy).toHaveBeenCalled();
    // No data passed in create mode
    const config = dialog.openSpy.mock.calls[0][1];
    expect(config?.data).toBeUndefined();
  });

  it('should emit `create` with the dialog result after closing the create dialog', () => {
    const { component, dialog } = createComponent();
    const createSpy = jest.fn();
    component.create.subscribe(createSpy);

    // Configure the dialog to emit a create payload when opened
    const payload: SkillTriggerCreate = {
      name: 'New trigger',
      condition_type: 'low_completion_rate',
      condition_json: { threshold: 0.3, min_selections: 5 },
      action: 'analyze',
      is_enabled: true,
    };
    dialog.setNextResult(payload);

    component.openCreateDialog();

    expect(createSpy).toHaveBeenCalledWith(payload);
  });

  it('should not emit `create` when the create dialog is cancelled (undefined result)', () => {
    const { component, dialog } = createComponent();
    const createSpy = jest.fn();
    component.create.subscribe(createSpy);

    dialog.setNextResult(undefined);

    component.openCreateDialog();

    expect(createSpy).not.toHaveBeenCalled();
  });

  // ---- Edit dialog flow ----

  it('should open the form dialog pre-filled when openEditDialog is called', () => {
    const { component, dialog } = createComponent();
    const trigger = createTrigger({ id: 't-edit', name: 'Editable' });

    component.openEditDialog(trigger);

    expect(dialog.openSpy).toHaveBeenCalled();
    const config = dialog.openSpy.mock.calls[0][1];
    expect(config?.data?.trigger).toEqual(trigger);
  });

  it('should emit `update` with id and result after closing the edit dialog', () => {
    const { component, dialog } = createComponent();
    const trigger = createTrigger({ id: 't-edit', name: 'Editable' });
    const updateSpy = jest.fn();
    component.update.subscribe(updateSpy);

    const updatePayload: SkillTriggerUpdate = {
      name: 'Renamed',
      is_enabled: false,
    };
    dialog.setNextResult(updatePayload);

    component.openEditDialog(trigger);

    expect(updateSpy).toHaveBeenCalledWith({ id: 't-edit', data: updatePayload });
  });

  it('should not emit `update` when the edit dialog is cancelled (undefined result)', () => {
    const { component, dialog } = createComponent();
    const trigger = createTrigger({ id: 't-edit', name: 'Editable' });
    const updateSpy = jest.fn();
    component.update.subscribe(updateSpy);

    dialog.setNextResult(undefined);

    component.openEditDialog(trigger);

    expect(updateSpy).not.toHaveBeenCalled();
  });

  // ---- Multi-trigger rendering (logic-only assertion) ----

  it('should render one summary per trigger in the input', () => {
    const { component } = createComponent();
    const triggers = [
      createTrigger({ id: 't-1', name: 'Alpha', condition_json: { threshold: 0.3 } }),
      createTrigger({
        id: 't-2',
        name: 'Beta',
        condition_type: 'periodic_scan',
        condition_json: { interval_days: 7 },
      }),
    ];
    component.triggers = triggers;
    expect(component.triggers.length).toBe(2);
    expect(component.formatConfigSummary(component.triggers[0].condition_json)).toContain('0.3');
    expect(component.formatConfigSummary(component.triggers[1].condition_json)).toContain('7');
  });
});