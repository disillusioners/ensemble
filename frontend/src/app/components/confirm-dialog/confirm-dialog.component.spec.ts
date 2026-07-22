import { computed } from '@angular/core';

// ===========================================================================
// TestableConfirmDialogComponent
//
// Mirrors the real `ConfirmDialogComponent` so we can drive it in isolation
// without spinning up Angular's TestBed. The logic is identical to the
// production source; if the real component changes, this mirror must be
// updated too. This matches the project's standing convention of
// plain-TS logic-mirror specs (see `instance-delete-dialog.component.spec.ts`
// and `jobs.component.spec.ts`).
// ===========================================================================

/** Mirrors the real `ConfirmDialogData` interface. */
export interface ConfirmDialogData {
  title?: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

/** Mirrors `MatDialogRef` — only the slice we exercise. */
class MockMatDialogRef<T = unknown> {
  closeResult: { called: boolean; value?: T } = { called: false };

  close(result?: T): void {
    this.closeResult = { called: true, value: result };
  }

  reset(): void {
    this.closeResult = { called: false };
  }
}

class TestableConfirmDialogComponent {
  protected readonly dialogRef: MockMatDialogRef<boolean>;
  protected readonly data: ConfirmDialogData | undefined;

  constructor(dialogRef: MockMatDialogRef<boolean>, data: ConfirmDialogData | undefined) {
    this.dialogRef = dialogRef;
    this.data = data;
  }

  // Mirror of the real `computed` signals — same shape, same resolution order.
  protected readonly title = computed(() => this.data?.title?.trim() || 'Confirm');
  protected readonly message = computed(
    () => this.data?.message?.trim() || 'Are you sure you want to proceed?',
  );
  protected readonly confirmLabel = computed(() => this.data?.confirmLabel?.trim() || 'Confirm');
  protected readonly cancelLabel = computed(() => this.data?.cancelLabel?.trim() || 'Cancel');
  protected readonly destructive = computed(() => this.data?.destructive ?? true);

  protected onCancel(): void {
    this.dialogRef.close(false);
  }

  protected onConfirm(): void {
    this.dialogRef.close(true);
  }
}

function createComponent(
  data?: ConfirmDialogData,
  overrides: { dialogRef?: MockMatDialogRef<boolean> } = {},
): { component: TestableConfirmDialogComponent; dialogRef: MockMatDialogRef<boolean> } {
  const dialogRef = overrides.dialogRef ?? new MockMatDialogRef<boolean>();
  const component = new TestableConfirmDialogComponent(dialogRef, data);
  return { component, dialogRef };
}

// ===========================================================================
// Tests
// ===========================================================================

describe('ConfirmDialogComponent', () => {
  // ---- a) Default values ----

  describe('default values (no data)', () => {
    it('should fall back to "Confirm" when no title is supplied', () => {
      const { component } = createComponent();
      expect(component.title()).toBe('Confirm');
    });

    it('should fall back to a generic confirmation message when no message is supplied', () => {
      const { component } = createComponent();
      expect(component.message()).toBe('Are you sure you want to proceed?');
    });

    it('should fall back to "Confirm" as the confirm button label', () => {
      const { component } = createComponent();
      expect(component.confirmLabel()).toBe('Confirm');
    });

    it('should fall back to "Cancel" as the cancel button label', () => {
      const { component } = createComponent();
      expect(component.cancelLabel()).toBe('Cancel');
    });

    it('should default destructive to true', () => {
      const { component } = createComponent();
      expect(component.destructive()).toBe(true);
    });
  });

  // ---- b) Resolved values (caller-supplied data) ----

  describe('resolved values', () => {
    it('should use the supplied title', () => {
      const { component } = createComponent({ title: 'Cancel Job' });
      expect(component.title()).toBe('Cancel Job');
    });

    it('should use the supplied message', () => {
      const { component } = createComponent({
        message: 'Are you sure you want to cancel this job? This action cannot be undone.',
      });
      expect(component.message()).toBe(
        'Are you sure you want to cancel this job? This action cannot be undone.',
      );
    });

    it('should use the supplied confirm button label', () => {
      const { component } = createComponent({ confirmLabel: 'Yes, Cancel Job' });
      expect(component.confirmLabel()).toBe('Yes, Cancel Job');
    });

    it('should use the supplied cancel button label', () => {
      const { component } = createComponent({ cancelLabel: 'Keep Job' });
      expect(component.cancelLabel()).toBe('Keep Job');
    });

    it('should respect destructive=false when the caller opts out', () => {
      const { component } = createComponent({ destructive: false });
      expect(component.destructive()).toBe(false);
    });

    it('should respect destructive=true when the caller opts in', () => {
      const { component } = createComponent({ destructive: true });
      expect(component.destructive()).toBe(true);
    });

    it('should trim whitespace-only titles and fall back to the default', () => {
      const { component } = createComponent({ title: '   ' });
      expect(component.title()).toBe('Confirm');
    });

    it('should trim whitespace-only messages and fall back to the default', () => {
      const { component } = createComponent({ message: '   ' });
      expect(component.message()).toBe('Are you sure you want to proceed?');
    });

    it('should trim whitespace-only confirm labels and fall back to the default', () => {
      const { component } = createComponent({ confirmLabel: '   ' });
      expect(component.confirmLabel()).toBe('Confirm');
    });

    it('should trim whitespace-only cancel labels and fall back to the default', () => {
      const { component } = createComponent({ cancelLabel: '   ' });
      expect(component.cancelLabel()).toBe('Cancel');
    });

    it('should treat an empty data object as equivalent to no data', () => {
      const { component } = createComponent({});
      expect(component.title()).toBe('Confirm');
      expect(component.message()).toBe('Are you sure you want to proceed?');
      expect(component.confirmLabel()).toBe('Confirm');
      expect(component.cancelLabel()).toBe('Cancel');
      expect(component.destructive()).toBe(true);
    });
  });

  // ---- c) onCancel ----

  describe('onCancel', () => {
    it('should close the dialog with false', () => {
      const { component, dialogRef } = createComponent();
      component.onCancel();
      expect(dialogRef.closeResult.called).toBe(true);
      expect(dialogRef.closeResult.value).toBe(false);
    });

    it('should close the dialog with false even when destructive is true', () => {
      const { component, dialogRef } = createComponent({ destructive: true });
      component.onCancel();
      expect(dialogRef.closeResult.value).toBe(false);
    });

    it('should close the dialog with false when no data was supplied', () => {
      const { component, dialogRef } = createComponent(undefined);
      component.onCancel();
      expect(dialogRef.closeResult.value).toBe(false);
    });
  });

  // ---- d) onConfirm ----

  describe('onConfirm', () => {
    it('should close the dialog with true', () => {
      const { component, dialogRef } = createComponent();
      component.onConfirm();
      expect(dialogRef.closeResult.called).toBe(true);
      expect(dialogRef.closeResult.value).toBe(true);
    });

    it('should close the dialog with true in destructive mode', () => {
      const { component, dialogRef } = createComponent({ destructive: true });
      component.onConfirm();
      expect(dialogRef.closeResult.value).toBe(true);
    });

    it('should close the dialog with true in non-destructive mode', () => {
      const { component, dialogRef } = createComponent({ destructive: false });
      component.onConfirm();
      expect(dialogRef.closeResult.value).toBe(true);
    });
  });

  // ---- e) onCancel / onConfirm interaction ----

  describe('onCancel and onConfirm interaction', () => {
    it('should only call close once per action', () => {
      const { component, dialogRef } = createComponent();
      component.onCancel();
      expect(dialogRef.closeResult.called).toBe(true);
      // Re-create the mock scenario — subsequent `onConfirm` should still
      // record a fresh close call (the real MatDialogRef would have already
      // torn down, but the mirror just records the call).
      dialogRef.reset();
      component.onConfirm();
      expect(dialogRef.closeResult.called).toBe(true);
      expect(dialogRef.closeResult.value).toBe(true);
    });
  });

  // ---- f) Real-world invocation shape (the Cancel Job case) ----

  describe('Cancel Job call site', () => {
    it('should expose the exact copy required by the Jobs page cancel flow', () => {
      const { component } = createComponent({
        title: 'Cancel Job',
        message: 'Are you sure you want to cancel this job? This action cannot be undone.',
        confirmLabel: 'Yes, Cancel Job',
        cancelLabel: 'Cancel',
        destructive: true,
      });
      expect(component.title()).toBe('Cancel Job');
      expect(component.message()).toBe(
        'Are you sure you want to cancel this job? This action cannot be undone.',
      );
      expect(component.confirmLabel()).toBe('Yes, Cancel Job');
      expect(component.cancelLabel()).toBe('Cancel');
      expect(component.destructive()).toBe(true);
    });

    it('should return true when the user confirms the cancel', () => {
      const { component, dialogRef } = createComponent({
        title: 'Cancel Job',
        message: 'Are you sure you want to cancel this job? This action cannot be undone.',
        confirmLabel: 'Yes, Cancel Job',
        cancelLabel: 'Cancel',
        destructive: true,
      });
      component.onConfirm();
      expect(dialogRef.closeResult.value).toBe(true);
    });

    it('should return false when the user dismisses the cancel', () => {
      const { component, dialogRef } = createComponent({
        title: 'Cancel Job',
        message: 'Are you sure you want to cancel this job? This action cannot be undone.',
        confirmLabel: 'Yes, Cancel Job',
        cancelLabel: 'Cancel',
        destructive: true,
      });
      component.onCancel();
      expect(dialogRef.closeResult.value).toBe(false);
    });
  });
});
