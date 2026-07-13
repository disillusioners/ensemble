import { signal, computed } from '@angular/core';
import { Observable } from 'rxjs';
import type { InstanceInfo } from '../../models';

// ---- Shared mocks (same style as instance-list + mcp-server-dialog specs) ----

// Mock MatSnackBar — records every `open()` call so tests can assert on it.
class MockMatSnackBar {
  static lastOpen: { message: string; action?: string; options?: object } | null = null;
  static history: Array<{ message: string; action?: string; options?: object }> = [];

  open(
    message: string,
    action?: string,
    options?: { duration?: number; panelClass?: string },
  ): void {
    const entry = { message, action, options };
    MockMatSnackBar.lastOpen = entry;
    MockMatSnackBar.history.push(entry);
  }

  static reset(): void {
    MockMatSnackBar.lastOpen = null;
    MockMatSnackBar.history = [];
  }
}

// Mock MatDialogRef — captures close() calls and exposed result to tests.
class MockMatDialogRef<T = unknown> {
  closeResult: { called: boolean; value?: T } = { called: false };

  close(result?: T): void {
    this.closeResult = { called: true, value: result };
  }

  /** Test helper: reset close state between cases. */
  reset(): void {
    this.closeResult = { called: false };
  }
}

// Mock ApiService — `deleteInstance(id, hardDelete)` returns an Observable that
// emits success or error depending on the test's configured outcome.
class MockApiService {
  readonly deleteInstanceCalls: Array<{ id: string; hardDelete: boolean }> = [];

  private mode: 'success' | 'error' = 'success';
  private errorToThrow: unknown = Object.assign(new Error('Boom'), {
    error: { detail: 'Delete failed' },
  });

  setResult(mode: 'success' | 'error', error?: unknown): void {
    this.mode = mode;
    if (error !== undefined) {
      this.errorToThrow = error;
    }
  }

  deleteInstance(id: string, hardDelete: boolean): Observable<{ terminated: boolean }> {
    this.deleteInstanceCalls.push({ id, hardDelete });

    const mode = this.mode;
    const errorToThrow = this.errorToThrow;

    // Synchronous emitter so .subscribe().next() runs before handleTerminate
    // returns. Mirrors what `of()` does after a microtask flush, but in a
    // jest-preset-angular zone-aware environment microtask timing is flaky
    // without explicit flushing — a direct Observable constructor avoids that.
    return new Observable<{ terminated: boolean }>((observer) => {
      if (mode === 'success') {
        observer.next({ terminated: true });
        observer.complete();
      } else {
        observer.error(errorToThrow);
      }
    });
  }
}

// Mock SseService — verify disconnect / clearEvents are wired up after delete.
class MockSseService {
  disconnectCalls = 0;
  clearEventsCalls = 0;

  disconnect(): void {
    this.disconnectCalls++;
  }

  clearEvents(): void {
    this.clearEventsCalls++;
  }
}

// Mock Router — tracks current URL and records `navigate` calls.
class MockRouter {
  currentUrl = '/projects/all/instances';
  navigateCalls: Array<{ commands: unknown[] }> = [];

  navigate(commands: unknown[]): Promise<boolean> {
    this.navigateCalls.push({ commands });
    return Promise.resolve(true);
  }
}

// Default dialog data shape (mirrors the real dialog data interface).
interface TestDialogData {
  instance: InstanceInfo;
}

// ===========================================================================
// TestableInstanceDeleteDialogComponent
//
// Mirrors the real `InstanceDeleteDialogComponent` so we can drive it in
// isolation without spinning up Angular's TestBed. Logic is identical to the
// production source; if the real component changes, this mirror must be
// updated too.
// ===========================================================================
class TestableInstanceDeleteDialogComponent {
  protected readonly view = signal<'primary' | 'confirm-delete'>('primary');
  protected readonly isBusy = signal(false);

  protected readonly displayLabel = computed(() => {
    const data = this.data as unknown as TestDialogData;
    const title = data.instance.title?.trim();
    if (title) return title;
    const id = data.instance.instance_id;
    return id.length > 12 ? `${id.slice(0, 12)}...` : id;
  });

  // Wired up in the constructor — same shape as the real `inject(...)` calls.
  protected readonly api: MockApiService;
  protected readonly snackBar: MockMatSnackBar;
  protected readonly sseService: MockSseService;
  protected readonly router: MockRouter;
  protected readonly dialogRef: MockMatDialogRef<boolean | { action: 'terminate' | 'delete' }>;
  protected readonly data: unknown;

  constructor(
    api: MockApiService,
    snackBar: MockMatSnackBar,
    sseService: MockSseService,
    router: MockRouter,
    dialogRef: MockMatDialogRef<boolean | { action: 'terminate' | 'delete' }>,
    data: TestDialogData,
  ) {
    this.api = api;
    this.snackBar = snackBar;
    this.sseService = sseService;
    this.router = router;
    this.dialogRef = dialogRef;
    this.data = data;
  }

  // ---- mirror of real component methods ----

  protected handleCancel(): void {
    if (this.isBusy()) return;
    this.dialogRef.close(false);
  }

  protected handleTerminate(): void {
    if (this.isBusy()) return;
    this.isBusy.set(true);

    this.api
      .deleteInstance((this.data as TestDialogData).instance.instance_id, false)
      .subscribe({
        next: () => {
          this.snackBar.open('Instance terminated', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.dialogRef.close({ action: 'terminate' });
        },
        error: (err: unknown) => {
          this.isBusy.set(false);
          const message = this.extractErrorMessage(err, 'Failed to terminate instance');
          this.snackBar.open(message, 'Close', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  protected handleChooseDelete(): void {
    if (this.isBusy()) return;
    this.view.set('confirm-delete');
  }

  protected handleBack(): void {
    if (this.isBusy()) return;
    this.view.set('primary');
  }

  protected handleConfirmDelete(): void {
    if (this.isBusy()) return;
    this.isBusy.set(true);

    this.api
      .deleteInstance((this.data as TestDialogData).instance.instance_id, true)
      .subscribe({
        next: () => {
          this.snackBar.open('Instance deleted', 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });

          const deletedInstanceId = (this.data as TestDialogData).instance.instance_id;
          this.sseService.disconnect();
          this.sseService.clearEvents();

          if (this.router.currentUrl.includes(`/instances/${deletedInstanceId}`)) {
            this.router.navigate(['/']);
          }

          this.dialogRef.close({ action: 'delete' });
        },
        error: (err: unknown) => {
          this.isBusy.set(false);
          const message = this.extractErrorMessage(err, 'Failed to delete instance');
          this.snackBar.open(message, 'Close', {
            duration: 5000,
            panelClass: 'error-snackbar',
          });
        },
      });
  }

  private extractErrorMessage(err: unknown, fallback: string): string {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object' && detail && 'message' in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === 'string') return message;
    }
    const candidate = err as { message?: string };
    return candidate?.message || fallback;
  }
}

// ---- Helpers ----

function createMockInstance(overrides: Partial<InstanceInfo> = {}): InstanceInfo {
  return {
    instance_id: 'inst-1234567890ab',
    agent_id: 'test-agent',
    status: 'running',
    parent_id: null,
    children: [],
    title: 'My Test Instance',
    created_at: new Date().toISOString(),
    updated_at: null,
    project_id: null,
    ...overrides,
  };
}

function createComponent(
  instance?: InstanceInfo,
  overrides: {
    api?: MockApiService;
    snackBar?: MockMatSnackBar;
    sseService?: MockSseService;
    router?: MockRouter;
    dialogRef?: MockMatDialogRef<boolean | { action: 'terminate' | 'delete' }>;
  } = {},
): {
  component: TestableInstanceDeleteDialogComponent;
  api: MockApiService;
  snackBar: MockMatSnackBar;
  sseService: MockSseService;
  router: MockRouter;
  dialogRef: MockMatDialogRef<boolean | { action: 'terminate' | 'delete' }>;
} {
  const api = overrides.api ?? new MockApiService();
  const snackBar = overrides.snackBar ?? new MockMatSnackBar();
  const sseService = overrides.sseService ?? new MockSseService();
  const router = overrides.router ?? new MockRouter();
  const dialogRef =
    overrides.dialogRef ??
    new MockMatDialogRef<boolean | { action: 'terminate' | 'delete' }>();
  const data: TestDialogData = { instance: instance ?? createMockInstance() };

  const component = new TestableInstanceDeleteDialogComponent(
    api,
    snackBar,
    sseService,
    router,
    dialogRef,
    data,
  );

  return { component, api, snackBar, sseService, router, dialogRef };
}

// ===========================================================================
// Tests
// ===========================================================================
describe('InstanceDeleteDialogComponent', () => {
  beforeEach(() => {
    MockMatSnackBar.reset();
  });

  // ---- a) Initialization ----

  describe('initialization', () => {
    it('should start on the primary view', () => {
      const { component } = createComponent();
      expect(component.view()).toBe('primary');
    });

    it('should start with isBusy false', () => {
      const { component } = createComponent();
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(false);
    });

    it('should expose the instance title via displayLabel when present', () => {
      const { component } = createComponent(
        createMockInstance({ title: 'A meaningful title' }),
      );
      expect((component as unknown as { displayLabel: () => string }).displayLabel()).toBe(
        'A meaningful title',
      );
    });

    it('should fall back to a truncated instance id when title is missing', () => {
      const { component } = createComponent(
        createMockInstance({ title: null, instance_id: 'abcdefghijklmnop' }),
      );
      // 16-char id → first 12 chars + ellipsis
      expect((component as unknown as { displayLabel: () => string }).displayLabel()).toBe(
        'abcdefghijkl...',
      );
    });

    it('should not truncate short instance ids', () => {
      const { component } = createComponent(
        createMockInstance({ title: null, instance_id: 'short' }),
      );
      expect((component as unknown as { displayLabel: () => string }).displayLabel()).toBe(
        'short',
      );
    });

    it('should trim whitespace-only titles and fall back to the id', () => {
      const { component } = createComponent(
        createMockInstance({ title: '   ', instance_id: 'inst-xyz' }),
      );
      expect((component as unknown as { displayLabel: () => string }).displayLabel()).toBe(
        'inst-xyz',
      );
    });
  });

  // ---- b) handleCancel ----

  describe('handleCancel', () => {
    it('should close the dialog with false', () => {
      const { component, dialogRef } = createComponent();
      component.handleCancel();
      expect(dialogRef.closeResult.called).toBe(true);
      expect(dialogRef.closeResult.value).toBe(false);
    });

    it('should not call the api', () => {
      const { component, api } = createComponent();
      component.handleCancel();
      expect(api.deleteInstanceCalls).toHaveLength(0);
    });

    it('should be a no-op while isBusy is true', () => {
      const { component, dialogRef } = createComponent();
      // simulate an in-flight request by setting isBusy directly
      (component as unknown as { isBusy: { set: (v: boolean) => void } }).isBusy.set(true);
      component.handleCancel();
      expect(dialogRef.closeResult.called).toBe(false);
    });
  });

  // ---- c) handleTerminate ----

  describe('handleTerminate', () => {
    it('should call api.deleteInstance with hardDelete=false', () => {
      const { component, api } = createComponent();
      api.setResult('success');
      component.handleTerminate();
      expect(api.deleteInstanceCalls).toEqual([
        { id: 'inst-1234567890ab', hardDelete: false },
      ]);
    });

    it('should close the dialog with { action: "terminate" } on success', () => {
      const { component, dialogRef } = createComponent();
      component.handleTerminate();
      expect(dialogRef.closeResult.called).toBe(true);
      expect(dialogRef.closeResult.value).toEqual({ action: 'terminate' });
    });

    it('should show a success snackbar on success', () => {
      const { component } = createComponent();
      component.handleTerminate();
      expect(MockMatSnackBar.history).toHaveLength(1);
      expect(MockMatSnackBar.history[0].message).toBe('Instance terminated');
    });

    it('should leave isBusy true on success (dialog is closing — flag stays set)', () => {
      const { component, api } = createComponent();
      api.setResult('success');
      component.handleTerminate();
      // Synchronous observable — by the time we get here the next() callback
      // has already run and the dialog has been closed. The real component does
      // NOT reset isBusy on success (the dialog is about to disappear anyway);
      // only the error path resets it. This test pins that behaviour so a
      // future refactor can't quietly flip it.
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(true);
    });

    it('should be a no-op while isBusy is already true', () => {
      const { component, api, dialogRef } = createComponent();
      (component as unknown as { isBusy: { set: (v: boolean) => void } }).isBusy.set(true);
      component.handleTerminate();
      expect(api.deleteInstanceCalls).toHaveLength(0);
      expect(dialogRef.closeResult.called).toBe(false);
    });

    it('should reset isBusy to false on api error', () => {
      const { component, api } = createComponent();
      api.setResult('error', Object.assign(new Error('Network failed'), {
        error: { detail: 'Network failed' },
      }));
      component.handleTerminate();
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(false);
    });

    it('should NOT close the dialog on api error', () => {
      const { component, api, dialogRef } = createComponent();
      api.setResult('error', Object.assign(new Error('Boom'), {
        error: { detail: 'Server unavailable' },
      }));
      component.handleTerminate();
      expect(dialogRef.closeResult.called).toBe(false);
    });

    it('should surface a snackbar with the server-provided detail on error', () => {
      const { component, api } = createComponent();
      api.setResult('error', Object.assign(new Error('Boom'), {
        error: { detail: 'Server unavailable' },
      }));
      component.handleTerminate();
      expect(MockMatSnackBar.history).toHaveLength(1);
      expect(MockMatSnackBar.history[0].message).toBe('Server unavailable');
    });

    it('should fall back to the generic message when no detail is present', () => {
      const { component, api } = createComponent();
      api.setResult('error', { message: 'Transport failure' });
      component.handleTerminate();
      expect(MockMatSnackBar.lastOpen?.message).toBe('Transport failure');
    });

    it('should fall back to the hard-coded label when error has no useful fields', () => {
      const { component, api } = createComponent();
      api.setResult('error', {});
      component.handleTerminate();
      expect(MockMatSnackBar.lastOpen?.message).toBe('Failed to terminate instance');
    });
  });

  // ---- d) handleChooseDelete ----

  describe('handleChooseDelete', () => {
    it('should switch view to confirm-delete', () => {
      const { component } = createComponent();
      expect(component.view()).toBe('primary');
      component.handleChooseDelete();
      expect(component.view()).toBe('confirm-delete');
    });

    it('should not call the api', () => {
      const { component, api } = createComponent();
      component.handleChooseDelete();
      expect(api.deleteInstanceCalls).toHaveLength(0);
    });

    it('should be a no-op while isBusy is true', () => {
      const { component } = createComponent();
      (component as unknown as { isBusy: { set: (v: boolean) => void } }).isBusy.set(true);
      component.handleChooseDelete();
      expect(component.view()).toBe('primary');
    });
  });

  // ---- e) handleConfirmDelete (hard delete) ----

  describe('handleConfirmDelete', () => {
    it('should call api.deleteInstance with hardDelete=true', () => {
      const { component, api } = createComponent();
      api.setResult('success');
      component.handleConfirmDelete();
      expect(api.deleteInstanceCalls).toEqual([
        { id: 'inst-1234567890ab', hardDelete: true },
      ]);
    });

    it('should close the dialog with { action: "delete" } on success', () => {
      const { component, dialogRef } = createComponent();
      component.handleConfirmDelete();
      expect(dialogRef.closeResult.called).toBe(true);
      expect(dialogRef.closeResult.value).toEqual({ action: 'delete' });
    });

    it('should disconnect the SSE channel after a successful delete', () => {
      const { component, sseService } = createComponent();
      component.handleConfirmDelete();
      expect(sseService.disconnectCalls).toBe(1);
      expect(sseService.clearEventsCalls).toBe(1);
    });

    it('should show a success snackbar on success', () => {
      const { component } = createComponent();
      component.handleConfirmDelete();
      expect(MockMatSnackBar.history).toHaveLength(1);
      expect(MockMatSnackBar.history[0].message).toBe('Instance deleted');
    });

    it('should NOT navigate to "/" when the current url is unrelated', () => {
      const { component, router } = createComponent();
      router.currentUrl = '/projects/all/instances';
      component.handleConfirmDelete();
      expect(router.navigateCalls).toHaveLength(0);
    });

    it('should navigate to "/" when the current url matches the deleted instance', () => {
      const { component, router } = createComponent(
        createMockInstance({ instance_id: 'inst-special-001' }),
      );
      router.currentUrl = '/projects/abc/instances/inst-special-001';
      component.handleConfirmDelete();
      expect(router.navigateCalls).toHaveLength(1);
      expect(router.navigateCalls[0].commands).toEqual(['/']);
    });

    it('should reset isBusy to false on api error', () => {
      const { component, api } = createComponent();
      api.setResult('error', Object.assign(new Error('Boom'), {
        error: { detail: 'Cannot delete' },
      }));
      component.handleConfirmDelete();
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(false);
    });

    it('should NOT close the dialog on api error', () => {
      const { component, api, dialogRef } = createComponent();
      api.setResult('error', Object.assign(new Error('Boom'), {
        error: { detail: 'Cannot delete' },
      }));
      component.handleConfirmDelete();
      expect(dialogRef.closeResult.called).toBe(false);
    });

    it('should NOT touch sseService on api error', () => {
      const { component, api, sseService } = createComponent();
      api.setResult('error', Object.assign(new Error('Boom'), {
        error: { detail: 'Cannot delete' },
      }));
      component.handleConfirmDelete();
      expect(sseService.disconnectCalls).toBe(0);
      expect(sseService.clearEventsCalls).toBe(0);
    });

    it('should surface an error snackbar on failure', () => {
      const { component, api } = createComponent();
      api.setResult('error', Object.assign(new Error('Boom'), {
        error: { detail: 'Cannot delete: instance busy' },
      }));
      component.handleConfirmDelete();
      expect(MockMatSnackBar.lastOpen?.message).toBe('Cannot delete: instance busy');
    });

    it('should be a no-op while isBusy is already true', () => {
      const { component, api, dialogRef } = createComponent();
      (component as unknown as { isBusy: { set: (v: boolean) => void } }).isBusy.set(true);
      component.handleConfirmDelete();
      expect(api.deleteInstanceCalls).toHaveLength(0);
      expect(dialogRef.closeResult.called).toBe(false);
    });
  });

  // ---- f) isBusy flag transitions ----

  describe('isBusy flag', () => {
    it('should stay true after a successful terminate (reset only on error)', () => {
      const { component } = createComponent();
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(false);
      component.handleTerminate();
      // See the note in `handleTerminate > should leave isBusy true on success`:
      // success does not clear the flag because the dialog closes immediately.
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(true);
    });

    it('should stay true after a successful hard delete (reset only on error)', () => {
      const { component } = createComponent();
      component.handleConfirmDelete();
      expect((component as unknown as { isBusy: () => boolean }).isBusy()).toBe(true);
    });

    it('should keep handleChooseDelete gated by isBusy after a successful terminate', () => {
      const { component } = createComponent();
      component.handleTerminate();
      // isBusy stays true on success — handleChooseDelete must remain a no-op.
      component.handleChooseDelete();
      expect(component.view()).toBe('primary');
    });
  });

  // ---- View transitions: primary <-> confirm-delete ----

  describe('view transitions', () => {
    it('handleBack should return the view to primary from confirm-delete', () => {
      const { component } = createComponent();
      component.handleChooseDelete();
      expect(component.view()).toBe('confirm-delete');
      component.handleBack();
      expect(component.view()).toBe('primary');
    });

    it('handleBack should be a no-op while isBusy is true', () => {
      const { component } = createComponent();
      component.handleChooseDelete();
      (component as unknown as { isBusy: { set: (v: boolean) => void } }).isBusy.set(true);
      component.handleBack();
      expect(component.view()).toBe('confirm-delete');
    });
  });

  // ---- Error message extraction ----

  describe('error message extraction', () => {
    it('should extract detail when it is a plain string', () => {
      const { component, api } = createComponent();
      api.setResult('error', { error: { detail: 'plain detail' } });
      component.handleTerminate();
      expect(MockMatSnackBar.lastOpen?.message).toBe('plain detail');
    });

    it('should extract detail.message when detail is an object', () => {
      const { component, api } = createComponent();
      api.setResult('error', { error: { detail: { message: 'inner msg' } } });
      component.handleTerminate();
      expect(MockMatSnackBar.lastOpen?.message).toBe('inner msg');
    });
  });
});
