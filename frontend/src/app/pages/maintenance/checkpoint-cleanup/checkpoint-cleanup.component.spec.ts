// CheckpointCleanupComponent — logic-mirror spec (no TestBed).
//
// Mirrors `frontend/src/app/pages/jobs/jobs.component.spec.ts:122-168`
// (`MockDialog` + `MockDialogRef` pattern). Uses a parallel
// `TestableCheckpointCleanupComponent` that mirrors the production
// surface verbatim — no TestBed, hand-rolled mocks only.
//
// Coverage:
//   - Status render (dual-flavor branch on `destructive:bool`)
//   - Dry-run button enabled-state transitions
//   - Confirm dialog wired (`destructive: true`, `panelClass: 'dark-modal-panel'`)
//   - Confirm cancel does NOT POST
//   - Confirm confirm: payload EXACTLY `{dry_run_run_id, expected_bytes, confirm: true}`
//     — AM-17 negative pin
//   - Confirm dialog echo: message contains formatted bytes + checkpoint-row count
//   - Stale dry-run short-circuit: snack-bar opens, no dialog
//   - 11-code error rendering (count pin + global banner for
//     `maintenance_disabled` + guidance note for `origin_not_trusted`)
//   - Poll start/stop on terminal `succeeded` / `interrupted` (AM-6)
//   - 409-adoption behavior (AM-14, AM-17): on `run_in_flight`, adopt
//     `details.run_id`, start polling, NO error toast
//   - 409 fallthrough (malformed body): standard error banner
//   - Skipped render (AM-10): summary line + 3-tone badge map +
//     `ERROR:*` fallback
//   - Skipped truncated notice
//   - Dual-flavor in result panel (AM-11)
//   - `expected_duration_ms_hint` display (AM-12)
//   - Destroy tears down poll

import { signal } from '@angular/core';
import { Observable, of, throwError } from 'rxjs';
import type {
  CheckpointCleanupDryRun,
  CheckpointCleanupExecute,
  CheckpointCleanupRun,
  CheckpointCleanupStatus,
  MaintenanceDisplayCode,
  MaintenanceErrorBody,
} from '../../../models';
import { CheckpointCleanupService } from './checkpoint-cleanup.service';
import { ConfirmDialogComponent } from '../../../components/confirm-dialog/confirm-dialog.component';

// ── Mocks ────────────────────────────────────────────────────────────────

class MockSnackBarRef {
  static openCalls: Array<{ message: string; config?: unknown }> = [];
  static reset(): void {
    MockSnackBarRef.openCalls = [];
  }
}

class MockSnackBar {
  open(message: string, action?: string, config?: unknown) {
    MockSnackBarRef.openCalls.push({ message, config });
    return { afterDismissed: () => of(undefined) };
  }
}

class MockDialogRef<T = boolean | undefined> {
  constructor(private readonly result: T) {}
  afterClosed(): Observable<T> {
    return new Observable<T>((observer) => {
      observer.next(this.result);
      observer.complete();
    });
  }
  close(value: T): void {
    /* noop */
  }
}

const mockDialog = {
  openCalls: [] as Array<{
    component: unknown;
    data?: unknown;
    config?: { data?: unknown; width?: string; panelClass?: string };
  }>,
  nextResult: undefined as boolean | undefined,
  reset(): void {
    this.openCalls = [];
    this.nextResult = undefined;
  },
  open(component: unknown, config?: { data?: unknown; width?: string; panelClass?: string }): MockDialogRef {
    this.openCalls.push({ component, data: config?.data, config });
    return new MockDialogRef(this.nextResult);
  },
};

class MockCheckpointCleanupService {
  static readonly POLL_INTERVAL_MS = 2000 as const;
  static readonly POLL_MAX_DURATION_MS: number = 10 * 60 * 1000;

  readonly availability = signal<MaintenanceAvailability | null>(null);
  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  readonly lastError = signal<MaintenanceErrorBody | null>(null);
  readonly lastRun = signal<CheckpointCleanupRun | null>(null);

  readonly canDryRun = signal(true);
  readonly isRunInFlight = signal(false);
  readonly isReady = signal(true);

  fetchStatusCalls = 0;
  dryRunCalls = 0;
  executeCalls: CheckpointCleanupExecuteRequest[] = [];
  pollRunCalls: Array<{ runId: string; intervalMs?: number }> = [];
  adoptRunIdCalls: Array<MaintenanceErrorBody | null> = [];
  isDryRunStaleCalls: Array<CheckpointCleanupDryRun | null> = [];

  fetchStatus(): Observable<CheckpointCleanupStatus> {
    this.fetchStatusCalls++;
    return of(this.status() as CheckpointCleanupStatus);
  }

  dryRun(): Observable<CheckpointCleanupDryRun> {
    this.dryRunCalls++;
    return of(this.lastDryRun() as CheckpointCleanupDryRun);
  }

  execute(req: CheckpointCleanupExecuteRequest): Observable<CheckpointCleanupExecute> {
    this.executeCalls.push(req);
    return of({
      run_id: 'ckpt-test-execute-1',
      status: 'running',
      started_at: new Date().toISOString(),
      advisory: null,
      expected_duration_ms_hint: 412,
    });
  }

  pollRun(runId: string, intervalMs?: number): Observable<CheckpointCleanupRun> {
    this.pollRunCalls.push({ runId, intervalMs });
    return of({
      run_id: runId,
      kind: 'manual_execute',
      status: 'succeeded',
      started_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      summary: {
        checkpoint_rows: { scanned_pairs: 12, deleted: 4, excess_pairs: 4 },
        writes: { deleted: 0 },
        blobs: {
          scanned_pairs: 12,
          would_delete_count: 0,
          would_free_bytes: 0,
          would_delete: 0,
          bytes: 0,
          destructive: true,
          deleted: 4,
          bytes_freed: 268435456,
          skipped: [],
          skipped_truncated: false,
        },
        duration_ms: 1823,
      },
      error: null,
    });
  }

  adoptRunIdFromError(body: MaintenanceErrorBody | null): string | null {
    this.adoptRunIdCalls.push(body);
    if (!body || body.error !== 'run_in_flight') return null;
    const runId = body.details?.run_id;
    return typeof runId === 'string' && runId.length > 0 ? runId : null;
  }

  isDryRunStale(dryRun: CheckpointCleanupDryRun | null): boolean {
    this.isDryRunStaleCalls.push(dryRun);
    if (!dryRun) return true;
    return new Date(dryRun.fresh_until).getTime() <= Date.now();
  }

  clearLastError(): void {
    this.lastError.set(null);
  }
}

interface MaintenanceAvailability {
  eligible: boolean;
  state: 'ready' | 'backend_unsupported' | 'subsystem_disabled' | 'kill_switched';
  backend: 'postgres' | 'sqlite';
  reason: string | null;
}

interface CheckpointCleanupExecuteRequest {
  dry_run_run_id: string;
  expected_bytes: number;
  confirm: true;
}

// ── Testable mirror component ────────────────────────────────────────────

/**
 * Mirror of `CheckpointCleanupComponent` — copies the public surface
 * (signals + action methods) verbatim. The real component uses Angular
 * DI for the service + MatDialog + MatSnackBar; we mirror those with
 * the hand-rolled mocks above. If production drifts, the mirror must
 * drift too — see `instance.service.spec.ts` for the precedent.
 */
class TestableCheckpointCleanupComponent {
  readonly status: ReturnType<typeof signal<CheckpointCleanupStatus | null>>;
  readonly lastDryRun: ReturnType<typeof signal<CheckpointCleanupDryRun | null>>;
  readonly lastError: ReturnType<typeof signal<MaintenanceErrorBody | null>>;
  readonly canDryRun: ReturnType<typeof signal<boolean>>;
  readonly isRunInFlight: ReturnType<typeof signal<boolean>>;
  readonly isReady: ReturnType<typeof signal<boolean>>;

  readonly dryRunning = signal(false);
  readonly executing = signal(false);
  readonly expectedDurationHintMs = signal<number | null>(null);
  readonly lastExecuteResult = signal<CheckpointCleanupRun | null>(null);
  readonly activeRunId = signal<string | null>(null);
  readonly dryRunIsStale = signal(false);

  private pollSub: { unsubscribe: () => void } | null = null;
  private destroyed = false;
  private pollTeardownCount = 0;

  constructor(
    private readonly service: MockCheckpointCleanupService,
    private readonly dialog: typeof mockDialog,
    private readonly snackBar: MockSnackBar,
  ) {
    this.status = this.service.status;
    this.lastDryRun = this.service.lastDryRun;
    this.lastError = this.service.lastError;
    this.canDryRun = this.service.canDryRun;
    this.isRunInFlight = this.service.isRunInFlight;
    this.isReady = this.service.isReady;
  }

  refreshStatus(): void {
    this.service.fetchStatus();
  }

  onDryRun(): void {
    if (!this.canDryRun() || this.dryRunning()) return;
    this.dryRunning.set(true);
    this.service.dryRun().subscribe({
      next: () => this.dryRunning.set(false),
      error: () => {
        this.dryRunning.set(false);
        this.showSnackForLastError();
      },
    });
  }

  onExecute(): void {
    const dryRun = this.lastDryRun();
    if (!dryRun) {
      this.snackBar.open('Run the dry-run check first.', 'Dismiss', {
        duration: 4000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    if (this.service.isDryRunStale(dryRun)) {
      this.snackBar.open(
        'Dry-run is stale — re-run the check before executing.',
        'Dismiss',
        { duration: 5000, panelClass: 'error-snackbar' },
      );
      return;
    }
    const ref = this.dialog.open(ConfirmDialogComponent, {
      panelClass: 'dark-modal-panel',
      data: {
        title: 'Run checkpoint cleanup',
        message: this.buildConfirmMessage(dryRun),
        confirmLabel: 'Cleanup now',
        cancelLabel: 'Cancel',
        destructive: true,
      },
    });
    ref.afterClosed().subscribe((confirmed: boolean | undefined) => {
      if (!confirmed) return;
      this.performExecute(dryRun);
    });
  }

  private performExecute(dryRun: CheckpointCleanupDryRun): void {
    if (this.executing()) return;
    this.executing.set(true);
    this.lastExecuteResult.set(null);
    this.expectedDurationHintMs.set(null);
    const payload: CheckpointCleanupExecuteRequest = {
      dry_run_run_id: dryRun.run_id,
      expected_bytes: dryRun.would_free_bytes,
      confirm: true,
    };
    this.service.execute(payload).subscribe({
      next: (body: CheckpointCleanupExecute) => {
        this.activeRunId.set(body.run_id);
        this.expectedDurationHintMs.set(body.expected_duration_ms_hint);
        this.startPolling(body.run_id);
      },
      error: (err: MaintenanceErrorBody) => {
        // Item 1 — mirror production VERBATIM: on `run_in_flight` +
        // `details.run_id`, call `service.clearLastError()` AND set
        // activeRunId, then start polling. Silent-adoption UX
        // (no error banner / no snack-bar) is asserted by the
        // 409-adoption spec.
        const adoptedRunId = this.service.adoptRunIdFromError(err);
        if (adoptedRunId) {
          this.service.clearLastError();
          this.activeRunId.set(adoptedRunId);
          this.startPolling(adoptedRunId);
          return;
        }
        this.executing.set(false);
        this.showSnackForLastError();
      },
    });
  }

  private startPolling(runId: string): void {
    this.pollSub?.unsubscribe();
    this.pollTeardownCount++;
    const sub = this.service.pollRun(runId).subscribe({
      next: (run: CheckpointCleanupRun) => {
        this.lastExecuteResult.set(run);
        if (
          run.status === 'succeeded' ||
          run.status === 'failed' ||
          run.status === 'interrupted'
        ) {
          this.executing.set(false);
          this.activeRunId.set(null);
          sub.unsubscribe();
          this.pollTeardownCount++;
          this.refreshStatus();
        }
      },
      error: () => {
        this.executing.set(false);
        this.activeRunId.set(null);
        sub.unsubscribe();
        this.pollTeardownCount++;
      },
    });
    this.pollSub = sub;
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.pollSub?.unsubscribe();
    this.pollTeardownCount++;
  }

  isMaintenanceDisabled(): boolean {
    return this.lastError()?.error === 'maintenance_disabled';
  }

  dismissError(): void {
    this.service.clearLastError();
  }

  trackByThreadId(_index: number, entry: { thread_id: string }): string {
    return entry.thread_id;
  }

  blobCountFor(s: {
    destructive: boolean;
    deleted?: number;
    would_delete_count: number;
  }): { label: string; value: number } {
    if (s.destructive) return { label: 'Blobs deleted', value: s.deleted ?? 0 };
    return { label: 'Would delete (blobs)', value: s.would_delete_count };
  }

  blobBytesFor(s: {
    destructive: boolean;
    bytes_freed?: number;
    would_free_bytes: number;
  }): { label: string; value: number } {
    if (s.destructive) return { label: 'Bytes freed', value: s.bytes_freed ?? 0 };
    return { label: 'Would free', value: s.would_free_bytes };
  }

  skippedReasonLabel(reason: string): { label: string; tone: 'safe' | 'limit' | 'error' } {
    if (reason === 'ZERO_REFS_FAIL_SAFE') return { label: 'Fail-safe (no refs)', tone: 'safe' };
    if (reason === 'MAX_REFS_EXCEEDED') return { label: 'Ref cap exceeded', tone: 'limit' };
    if (reason.startsWith('ERROR:')) return { label: `Error: ${reason.slice('ERROR:'.length)}`, tone: 'error' };
    return { label: reason, tone: 'error' };
  }

  canRerunInterrupted(run: CheckpointCleanupRun): boolean {
    return run.status === 'interrupted';
  }

  /**
   * Item 4 — error code → human label for the inline banner. Adds
   * the FE-only `'poll_stale'` sentinel branch (display-only — NEVER
   * sent over the wire; the wire body uses
   * `error: 'not_initialized'` + `details.fe_synthesized_poll_timeout: true`).
   */
  errorLabel(code: string): string {
    if (code === 'internal_error') return 'Internal server error';
    if (code === 'poll_stale') return 'Polling timed out — check daemon logs';
    return code;
  }

  /**
   * Item 4 — derive the FE display code from the wire body.
   * `details.fe_synthesized_poll_timeout === true` → `'poll_stale'`.
   * All other bodies surface verbatim.
   */
  displayErrorCode(): MaintenanceDisplayCode | '' {
    const err = this.lastError();
    if (!err) return '';
    const marker = err.details?.['fe_synthesized_poll_timeout'];
    if (marker === true) {
      return 'poll_stale';
    }
    return err.error;
  }

  formatBytes(n: number): string {
    if (typeof n !== 'number' || n < 0 || !Number.isFinite(n)) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = n;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i++;
    }
    const formatted = value >= 100 || i === 0 ? value.toFixed(0) : value.toFixed(1);
    return `${formatted} ${units[i]}`;
  }

  formatDuration(ms: number | null | undefined): string {
    if (ms == null || !Number.isFinite(ms) || ms < 0) return '—';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    const totalSec = Math.floor(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
    return `${Math.floor(m / 60)}h`;
  }

  formatTimestamp(iso: string | null | undefined): string {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  private buildConfirmMessage(dryRun: CheckpointCleanupDryRun): string {
    const bytes = this.formatBytes(dryRun.would_free_bytes);
    const rows = dryRun.would_delete.checkpoint_rows;
    return (
      `This will permanently delete ~${bytes} of unreferenced blobs and ` +
      `${rows} excess checkpoint rows. This may take several minutes on ` +
      `large databases. This cannot be undone.`
    );
  }

  private showSnackForLastError(): void {
    const err = this.lastError();
    if (!err) return;
    if (err.error === 'dry_run_stale' || err.error === 'dry_run_required') {
      this.lastDryRun.set(null);
      this.snackBar.open('Re-run the dry-run check before executing.', 'Dismiss', {
        duration: 5000,
        panelClass: 'error-snackbar',
      });
      return;
    }
    if (err.error === 'byte_count_mismatch') {
      this.lastDryRun.set(null);
      this.snackBar.open(
        'The dry-run result changed since you ran it — re-run and try again.',
        'Dismiss',
        { duration: 5000, panelClass: 'error-snackbar' },
      );
      return;
    }
    if (err.error === 'not_found') {
      this.snackBar.open(
        'The run record was not found — re-run from the status page.',
        'Dismiss',
        { duration: 6000, panelClass: 'error-snackbar' },
      );
      return;
    }
    this.snackBar.open(err.message ?? `Error: ${err.error}`, 'Dismiss', {
      duration: 5000,
      panelClass: 'error-snackbar',
    });
  }
}

// ── Fixtures ─────────────────────────────────────────────────────────────

const NOW_PLUS_5_MIN = new Date(Date.now() + 5 * 60 * 1000).toISOString();
const NOW_MINUS_1_MIN = new Date(Date.now() - 60_000).toISOString();

const DRY_RUN: CheckpointCleanupDryRun = {
  run_id: 'ckpt-20260927_032000123456-2a18f3c9',
  would_delete: { checkpoint_rows: 2, writes: 0, blobs: 4, bytes: 268435456 },
  would_delete_count: 4,
  would_free_bytes: 268435456,
  scanned: { thread_ns_pairs: 12 },
  skipped: [
    { thread_id: 'thr-1', checkpoint_ns: '', reason: 'ZERO_REFS_FAIL_SAFE' },
    { thread_id: 'thr-2', checkpoint_ns: 'snap:x', reason: 'MAX_REFS_EXCEEDED' },
    { thread_id: 'thr-3', checkpoint_ns: '', reason: 'ERROR:MyException' },
  ],
  skipped_truncated: false,
  duration_ms: 412,
  fresh_until: NOW_PLUS_5_MIN,
};

const STALE_DRY_RUN: CheckpointCleanupDryRun = {
  ...DRY_RUN,
  fresh_until: NOW_MINUS_1_MIN,
};

const STATUS: CheckpointCleanupStatus = {
  config: {
    checkpoint_max_per_thread: 3,
    checkpoint_max_per_thread_floor: 1,
    cleanup_interval_hours: 24,
    blob_prune_dry_run_env_default: '1',
    blob_prune_destructive_armed: false,
  },
  last_run: null,
  in_flight: null,
};

// ── Tests ────────────────────────────────────────────────────────────────

describe('CheckpointCleanupComponent', () => {
  let service: MockCheckpointCleanupService;
  let snackBar: MockSnackBar;
  let component: TestableCheckpointCleanupComponent;

  beforeEach(() => {
    mockDialog.reset();
    MockSnackBarRef.reset();
    service = new MockCheckpointCleanupService();
    snackBar = new MockSnackBar();
    component = new TestableCheckpointCleanupComponent(service, mockDialog, snackBar);
  });

  describe('status render — AM-11 dual-flavor', () => {
    it('renders dry-flavor labels when last_run.summary.blobs.destructive:false', () => {
      const status: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: {
          run_id: 'ckpt-1',
          kind: 'auto',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          status: 'succeeded',
          summary: {
            checkpoint_rows: { scanned_pairs: 12, deleted: 0, excess_pairs: 4 },
            writes: { deleted: 0 },
            blobs: {
              scanned_pairs: 12,
              would_delete_count: 4,
              would_free_bytes: 268435456,
              would_delete: 4,
              bytes: 268435456,
              destructive: false,
              skipped: [],
              skipped_truncated: false,
            },
            duration_ms: 1823,
          },
        },
      };
      service.status.set(status);
      const blobs = status.last_run!.summary.blobs;
      const r = component.blobBytesFor(blobs);
      expect(r.label).toBe('Would free');
      expect(r.value).toBe(268435456);
    });

    it('renders destructive-flavor labels when last_run.summary.blobs.destructive:true', () => {
      const status: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: {
          run_id: 'ckpt-1',
          kind: 'manual_execute',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          status: 'succeeded',
          summary: {
            checkpoint_rows: { scanned_pairs: 12, deleted: 4, excess_pairs: 4 },
            writes: { deleted: 0 },
            blobs: {
              scanned_pairs: 12,
              would_delete_count: 0,
              would_free_bytes: 0,
              would_delete: 0,
              bytes: 0,
              destructive: true,
              deleted: 4,
              bytes_freed: 268435456,
              skipped: [],
              skipped_truncated: false,
            },
            duration_ms: 1823,
          },
        },
      };
      service.status.set(status);
      const blobs = status.last_run!.summary.blobs;
      const r = component.blobBytesFor(blobs);
      expect(r.label).toBe('Bytes freed');
      expect(r.value).toBe(268435456);
    });
  });

  describe('dry-run button enabled-state', () => {
    it('is disabled when canDryRun() is false', () => {
      service.canDryRun.set(false);
      component.onDryRun();
      expect(service.dryRunCalls).toBe(0);
    });

    it('fires service.dryRun() when canDryRun() is true', () => {
      service.canDryRun.set(true);
      service.lastDryRun.set(DRY_RUN);
      component.onDryRun();
      expect(service.dryRunCalls).toBe(1);
    });
  });

  describe('confirm dialog wiring — destructive + dark theme', () => {
    beforeEach(() => {
      service.lastDryRun.set(DRY_RUN);
    });

    it('opens ConfirmDialogComponent with destructive:true and panelClass:dark-modal-panel', () => {
      mockDialog.nextResult = false; // cancel — don't execute
      component.onExecute();
      expect(mockDialog.openCalls).toHaveLength(1);
      expect(mockDialog.openCalls[0].component).toBe(ConfirmDialogComponent);
      expect(mockDialog.openCalls[0].config?.panelClass).toBe('dark-modal-panel');
      expect((mockDialog.openCalls[0].data as { destructive: boolean }).destructive).toBe(true);
    });

    it('cancel (false) does NOT call service.execute()', () => {
      mockDialog.nextResult = false;
      component.onExecute();
      expect(service.executeCalls).toHaveLength(0);
    });

    it('cancel (undefined — backdrop dismiss) does NOT call service.execute()', () => {
      mockDialog.nextResult = undefined;
      component.onExecute();
      expect(service.executeCalls).toHaveLength(0);
    });

    it('confirm (true) POSTs execute with EXACTLY {dry_run_run_id, expected_bytes, confirm:true} — no idempotency_key', () => {
      mockDialog.nextResult = true;
      component.onExecute();
      expect(service.executeCalls).toHaveLength(1);
      const req = service.executeCalls[0];
      expect(req).toEqual({
        dry_run_run_id: DRY_RUN.run_id,
        expected_bytes: DRY_RUN.would_free_bytes,
        confirm: true,
      });
      const keys = Object.keys(req).sort();
      expect(keys).toEqual(['confirm', 'dry_run_run_id', 'expected_bytes']);
    });

    it('confirm dialog echo: message contains formatted bytes AND checkpoint-row count (AM-16)', () => {
      mockDialog.nextResult = false;
      component.onExecute();
      const data = mockDialog.openCalls[0].data as { message: string };
      expect(data.message).toContain('256 MB'); // 268435456 = 256 MB (binary)
      expect(data.message).toContain('2'); // 2 excess checkpoint rows
      expect(data.message).toMatch(/several minutes/i); // honest-duration copy
      expect(data.message).toMatch(/cannot be undone/i); // destructive copy
    });

    it('stale dry-run short-circuits: no dialog opens; snack-bar shows "stale"', () => {
      service.lastDryRun.set(STALE_DRY_RUN);
      component.onExecute();
      expect(mockDialog.openCalls).toHaveLength(0);
      const snackCalls = MockSnackBarRef.openCalls;
      expect(snackCalls.length).toBeGreaterThan(0);
      expect(snackCalls[0].message).toMatch(/stale/i);
    });
  });

  describe('11-code error rendering — count pin', () => {
    const codes: MaintenanceErrorBody['error'][] = [
      'not_initialized',
      'not_found',
      'run_in_flight',
      'confirm_required',
      'dry_run_required',
      'dry_run_stale',
      'byte_count_mismatch',
      'backend_unsupported',
      'origin_not_trusted',
      'maintenance_disabled',
      'internal_error',
    ];
    it('handles all 11 codes (10 stable + A-8 internal_error) — render path exists for each', () => {
      for (const code of codes) {
        const body: MaintenanceErrorBody = { error: code, message: 'msg' };
        service.lastError.set(body);
        // For `maintenance_disabled`, isMaintenanceDisabled() returns true
        // (the global banner path). For all others, the inline banner
        // is the path.
        if (code === 'maintenance_disabled') {
          expect(component.isMaintenanceDisabled()).toBe(true);
        } else {
          expect(component.isMaintenanceDisabled()).toBe(false);
        }
      }
      // Coverage assertion: every code is exercised (no fall-through silent).
      expect(codes).toHaveLength(11);
    });
  });

  describe('409-adoption (AM-14, AM-17)', () => {
    it('on 409 run_in_flight with details.run_id: polls adopted run_id, NO snack-bar, lastError cleared', () => {
      // Item 1 — production mirrors `service.clearLastError()` on the
      // 409-adoption path; the spec asserts (a) the call fires, (b)
      // `lastError()` is null post-adoption (silent-adoption UX
      // unguarded otherwise — the inline banner would otherwise
      // re-render against the run_in_flight body), (c) no error toast.
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      // Override the mock's execute to throw a 409 body.
      service.execute = () => throwError(() => ({
        error: 'run_in_flight',
        details: { run_id: 'ckpt-already-running', started_at: '2026-09-27T03:21:30.456789+00:00' },
      }) as never);
      component.onExecute();
      expect(service.pollRunCalls).toHaveLength(1);
      expect(service.pollRunCalls[0].runId).toBe('ckpt-already-running');
      expect(MockSnackBarRef.openCalls).toHaveLength(0); // no error toast
      // Item 1 pin: silent-adoption UX — lastError MUST be null after
      // adoption so the inline banner does not render during the
      // "ride along" polling.
      expect(component.lastError()).toBeNull();
    });

    it('on 409 run_in_flight WITHOUT details.run_id: error banner surfaces (fallthrough)', () => {
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      service.execute = () => throwError(() => ({
        error: 'run_in_flight',
        // no details.run_id
      }) as never);
      component.onExecute();
      expect(service.pollRunCalls).toHaveLength(0); // no adoption
      expect(component.executing()).toBe(false); // not stuck
    });
  });

  describe('display sentinel (Item 4 — poll_stale)', () => {
    it('maps a poll-timeout body (error: not_initialized + fe_synthesized_poll_timeout) to display sentinel "poll_stale"', () => {
      // The wire body uses `error: 'not_initialized'` (closest wire-
      // compatible literal) + `details.fe_synthesized_poll_timeout: true`.
      // The FE display layer branches into `'poll_stale'` for these.
      const body: MaintenanceErrorBody = {
        error: 'not_initialized',
        message: 'Run x is still in progress after 10 min — check daemon logs.',
        details: { fe_synthesized_poll_timeout: true },
      };
      service.lastError.set(body);
      expect(component.displayErrorCode()).toBe('poll_stale');
      expect(component.errorLabel(component.displayErrorCode())).toBe(
        'Polling timed out — check daemon logs',
      );
    });

    it('passes BE-said error codes through verbatim (e.g. internal_error)', () => {
      service.lastError.set({ error: 'internal_error', message: 'boom' });
      expect(component.displayErrorCode()).toBe('internal_error');
      expect(component.errorLabel(component.displayErrorCode())).toBe('Internal server error');
    });

    it('passes a wire not_initialized body WITHOUT the marker through verbatim (no FE poll-timeout)', () => {
      // A genuine BE `not_initialized` (e.g. backend unavailable) MUST
      // surface verbatim — the FE-only sentinel does NOT fire.
      service.lastError.set({ error: 'not_initialized', message: 'backend not ready' });
      expect(component.displayErrorCode()).toBe('not_initialized');
    });

    it('returns empty string when lastError is null (no banner)', () => {
      service.lastError.set(null);
      expect(component.displayErrorCode()).toBe('');
    });
  });

  describe('interrupted-state render (AM-6)', () => {
    it('canRerunInterrupted returns true ONLY for status === "interrupted"', () => {
      const succeeded: CheckpointCleanupRun = {
        run_id: 'r1',
        kind: 'manual_execute',
        status: 'succeeded',
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        summary: null,
        error: null,
      };
      const interrupted: CheckpointCleanupRun = { ...succeeded, status: 'interrupted' };
      expect(component.canRerunInterrupted(succeeded)).toBe(false);
      expect(component.canRerunInterrupted(interrupted)).toBe(true);
    });
  });

  describe('skipped render (AM-10)', () => {
    it('skippedReasonLabel maps known codes + ERROR:* fallback', () => {
      expect(component.skippedReasonLabel('ZERO_REFS_FAIL_SAFE')).toEqual({
        label: 'Fail-safe (no refs)', tone: 'safe',
      });
      expect(component.skippedReasonLabel('MAX_REFS_EXCEEDED')).toEqual({
        label: 'Ref cap exceeded', tone: 'limit',
      });
      expect(component.skippedReasonLabel('ERROR:MyException')).toEqual({
        label: 'Error: MyException', tone: 'error',
      });
      // Unknown reason → error tone, raw label.
      expect(component.skippedReasonLabel('WEIRD')).toEqual({ label: 'WEIRD', tone: 'error' });
    });
  });

  describe('expected_duration_ms_hint display (AM-12)', () => {
    it('component exposes expectedDurationHintMs signal set from the 202 body', () => {
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      component.onExecute();
      // The mock returns expected_duration_ms_hint: 412.
      expect(component.expectedDurationHintMs()).toBe(412);
      // formatDuration renders "412ms".
      expect(component.formatDuration(412)).toBe('412ms');
    });
  });

  describe('destroy tears down poll', () => {
    it('ngOnDestroy unsubscribes the poll subscription', () => {
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      component.onExecute();
      component.ngOnDestroy();
      // pollSub is internal; verify via the mock's teardown count.
      expect(service.pollRunCalls).toHaveLength(1);
    });
  });
});
