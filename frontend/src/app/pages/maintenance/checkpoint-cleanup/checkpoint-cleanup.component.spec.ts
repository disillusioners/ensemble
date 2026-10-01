// CheckpointCleanupComponent — logic-mirror spec (no TestBed).
//
// Mirrors `frontend/src/app/pages/jobs/jobs.component.spec.ts:122-168`
// (`MockDialog` + `MockDialogRef` pattern). Uses a parallel
// `TestableCheckpointCleanupComponent` that mirrors the production
// surface verbatim — no TestBed, hand-rolled mocks only.
//
// Size rationale (v3.2 fix pass): this file crossed 1000 lines. The
// density is contract-fidelity, not bloat — every AM-/R-/pin- numbered
// behavior listed in the maintenance console plan + the v3.2 amendment
// has a corresponding spec case here (AM-1, AM-6, AM-10, AM-11, AM-12,
// AM-13, AM-14, AM-16, AM-17 + R-1, R-4, R-5 + Pins 1–18 split between
// this file and `maintenance.bindings.pins.spec.ts`). Extracting these
// into helper modules risks drift between the mirror and the
// production class — the value of this file IS the verbatim mirror
// of the production surface. Keep it dense.
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

import { CUSTOM_ELEMENTS_SCHEMA, NO_ERRORS_SCHEMA, signal, computed } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { BreakpointObserver } from '@angular/cdk/layout';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatStepperModule } from '@angular/material/stepper';
import { Observable, of, throwError } from 'rxjs';
import type {
  CheckpointCleanupDryRun,
  CheckpointCleanupExecute,
  CheckpointCleanupExecuteRequest,
  CheckpointCleanupRun,
  CheckpointCleanupStatus,
  MaintenanceDisplayCode,
  MaintenanceErrorBody,
} from '../../../models';
import { CheckpointCleanupService } from './checkpoint-cleanup.service';
import { CheckpointCleanupComponent } from './checkpoint-cleanup.component';
import { ConfirmDialogComponent } from '../../../components/confirm-dialog/confirm-dialog.component';
import {
  DRY_RUN,
  STALE_DRY_RUN,
  STATUS,
  DRY_RUN_NEVER_PRUNED,
  DRY_RUN_PRUNED,
  DRY_RUN_MIXED,
  DRY_RUN_WITH_SKIPPED,
  RUN_SUCCEEDED_NEVER_PRUNED,
  RUN_SUCCEEDED_PRUNED,
  RUN_AUTO_NO_PROJECTION,
  RUN_FAILED_NEVER_PRUNED,
  RUN_INTERRUPTED_NEVER_PRUNED,
  STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
  STATUS_LAST_RUN_AUTO_SUCCEEDED,
  STATUS_LAST_RUN_SUCCEEDED_AFTER_ZERO,
} from './__fixtures__/fixtures';

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
  // Fix commission 2026-09-29 — replaced `POLL_MAX_DURATION_MS`
  // (hard 10-min cap → synthesized poll-timeout error) with the
  // hint-derived budget constants + backoff helpers. Mirrors the
  // production service's surface; tests for the budget/backoff
  // behavior live in `service.spec.ts` and are exercised here only
  // to verify the component wires the hint through correctly.
  static readonly HINT_MULTIPLIER = 2 as const;
  static readonly POLL_BUDGET_FLOOR_MS: number = 15 * 60 * 1000;
  static readonly BACKOFF_STEP_MS: number = 2000;
  static readonly BACKOFF_CEILING_MS: number = 30_000;

  readonly status = signal<CheckpointCleanupStatus | null>(null);
  readonly lastDryRun = signal<CheckpointCleanupDryRun | null>(null);
  readonly lastError = signal<MaintenanceErrorBody | null>(null);
  // Item 12 — `availability` / `lastRun` / `isReady` are dead
  // surface in production; the spec mirror drops them too.
  // `canDryRun` / `isRunInFlight` are still live (consumed by the
  // component's button-disabled guards).

  readonly canDryRun = signal(true);
  readonly isRunInFlight = signal(false);

  fetchStatusCalls = 0;
  dryRunCalls = 0;
  executeCalls: CheckpointCleanupExecuteRequest[] = [];
  pollRunCalls: Array<{ runId: string; intervalMs?: number; hintMs?: number | null }> = [];
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

  pollRun(
    runId: string,
    intervalMs?: number,
    hintMs?: number | null,
  ): Observable<CheckpointCleanupRun> {
    // Fix commission 2026-09-29 — record the 3rd arg so the
    // component-hint-plumbing spec can assert it.
    this.pollRunCalls.push({ runId, intervalMs, hintMs });
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

// Item 15 — `CheckpointCleanupExecuteRequest` already exists in
// `../../../models`. The local type shadow was a duplicate
// definition; import the canonical type instead so the spec cannot
// drift from production shapes.

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
  // v3.2 B1 — anchor for the post-run banner's "hide on convergence"
  // rule. Mirrors production: set in performExecute; the hide check
  // scopes by `lastDryRun.run_id !== executedDryRunId`. See
  // showRunAgainBanner() in the production class for the rationale.
  readonly executedDryRunId = signal<string | null>(null);
  readonly dryRunIsStale = signal(false);

  // ── Wizard state (ck-redesign-2026q4) — mirror production verbatim. ──
  readonly activeStep = signal(0);
  readonly stepperNav = () => ({
    next: () => this.activeStep.update((i) => Math.min(i + 1, 3)),
    back: () => this.activeStep.update((i) => Math.max(i - 1, 0)),
    backToStart: () => this.activeStep.set(0),
  });
  // C1 — `isDesktop` signal drives the @if/@else orientation switch
  // in the template. Production wires this to BreakpointObserver;
  // the mirror's default is `false` (vertical orientation, narrow).
  // Spec calls test via direct mutation of this signal.
  readonly isDesktop = signal(false);
  /**
   * W1 — selectionChange handler. Mirrors production: clamps to the
   * 0..3 valid step range and updates `activeStep()` from the
   * stepper's `selectedIndex`. Header-driven jumps propagate here;
   * the footer's `activeStep()` branch reads pick up the new index
   * and render the right buttons.
   */
  onStepperSelectionChange(stepper: { selectedIndex: number } | undefined): void {
    if (!stepper) return;
    const idx = stepper.selectedIndex;
    if (idx < 0 || idx > 3) return;
    if (idx !== this.activeStep()) {
      this.activeStep.set(idx);
    }
  }
  // canContinueFromStep1 / Step2 / Step3 are pure functions of the
  // service-exposed signals + local UI state. Mirror production.

  private pollSub: { unsubscribe: () => void; closed: boolean } | null = null;
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
    // Item 12 — `isReady` re-exposed alias removed (template never
    // reads it; production service also dropped the computed).
  }

  refreshStatus(): void {
    // v3.2 B2 — mirror production: subscribe to the status response
    // and seed the banner from `status.last_run` when applicable.
    // Fix commission 2026-09-29 — also resume polling against
    // `status.in_flight.run_id` so a page refresh mid-poll recovers
    // into live tracking. Mirrors production's
    // `resumePollingIfInFlight()` branch.
    this.service.fetchStatus().subscribe({
      next: (status: CheckpointCleanupStatus) => {
        this.seedBannerFromStatus(status);
        this.resumePollingIfInFlight(status);
      },
      error: () => {
        // Already surfaced via service.lastError.
      },
    });
  }

  /**
   * Fix commission 2026-09-29 — re-entry resume. Mirrors
   * production verbatim. On `refreshStatus()` landing, if
   * `status.in_flight` carries a `run_id`, start polling it.
   * Idempotent against a same-id poll already in flight —
   * `activeRunId()` is the source of truth.
   *
   * Iter2 review finding 2 — re-entry feedback-loop guard: if
   * `lastExecuteResult()` already carries a terminal row for the
   * SAME `run_id`, skip the resume. The terminal handler has
   * already fired for this id and nothing more can be learned.
   * Without the guard, a BE-side `in_flight`-retained window
   * would cause refreshStatus → resume → poll-terminal →
   * terminal-handler → refreshStatus to loop until the BE
   * clears the slot.
   */
  private resumePollingIfInFlight(status: CheckpointCleanupStatus): void {
    const inFlight = status.in_flight;
    if (!inFlight || !inFlight.run_id) {
      return;
    }
    if (this.activeRunId() === inFlight.run_id) {
      return;
    }
    // Feedback-loop guard — see class JSDoc above.
    const last = this.lastExecuteResult();
    if (
      last &&
      last.run_id === inFlight.run_id &&
      (last.status === 'succeeded' ||
        last.status === 'failed' ||
        last.status === 'interrupted')
    ) {
      return;
    }
    this.service.clearLastError();
    this.activeRunId.set(inFlight.run_id);
    this.startPolling(inFlight.run_id);
  }

  /**
   * v3.2 B2 — banner persistence across page refresh. Seeds
   * `lastExecuteResult` from `status.last_run` when the row is a
   * `manual_execute` that succeeded and carries a projection block
   * with `bytes_reclaimable_after_row_prune_at_dry_run > 0`.
   * Mirrors production verbatim. MUST NOT touch
   * `executedDryRunId` — the anchor is `performExecute`-owned.
   */
  private seedBannerFromStatus(status: CheckpointCleanupStatus): void {
    const last = status.last_run;
    if (!last) return;
    if (last.kind !== 'manual_execute') return;
    if (last.status !== 'succeeded') return;
    if (!last.summary) return;
    const after =
      last.summary.projection?.bytes_reclaimable_after_row_prune_at_dry_run ?? 0;
    if (after <= 0) return;
    this.lastExecuteResult.set({
      run_id: last.run_id,
      kind: last.kind,
      status: last.status,
      started_at: last.started_at,
      completed_at: last.completed_at,
      summary: last.summary,
      error: null,
    });
    // Anchor intentionally untouched (B1 follow-up — wiping it here
    // would regress the live-execute row-(a) scenario). Mirrors
    // production.
  }

  onDryRun(): void {
    if (!this.canDryRun() || this.dryRunning()) return;
    this.dryRunning.set(true);
    // Item 14 — optimistic lastError clear (mirror production).
    this.service.clearLastError();
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
      // Item 6 — re-check isDryRunStale BEFORE performExecute (the
      // dialog may have been open long enough for fresh_until to
      // expire). Mirror production's behavior verbatim.
      if (this.service.isDryRunStale(dryRun)) {
        this.lastDryRun.set(null);
        this.snackBar.open(
          'Dry-run is stale — re-run the check before executing.',
          'Dismiss',
          { duration: 5000, panelClass: 'error-snackbar' },
        );
        return;
      }
      this.performExecute(dryRun);
    });
  }

  private performExecute(dryRun: CheckpointCleanupDryRun): void {
    if (this.executing()) return;
    this.executing.set(true);
    this.lastExecuteResult.set(null);
    this.expectedDurationHintMs.set(null);
    // Item 14 — optimistic lastError clear (mirror production).
    this.service.clearLastError();
    // v3.2 B1 — anchor the banner's hide-on-convergence rule.
    // Mirrors production's performExecute verbatim.
    this.executedDryRunId.set(dryRun.run_id);
    const payload: CheckpointCleanupExecuteRequest = {
      dry_run_run_id: dryRun.run_id,
      expected_bytes: dryRun.would_free_bytes,
      confirm: true,
    };
    this.service.execute(payload).subscribe({
      next: (body: CheckpointCleanupExecute) => {
        this.activeRunId.set(body.run_id);
        this.expectedDurationHintMs.set(body.expected_duration_ms_hint);
        // Fix commission 2026-09-29 — pipe the 202 body's
        // `expected_duration_ms_hint` through to `startPolling`
        // so the service can size the active poll budget
        // (`max(hint × N, floor)`) per the new contract.
        this.startPolling(body.run_id, body.expected_duration_ms_hint);
      },
      error: (err: MaintenanceErrorBody) => {
        // Item 1 — mirror production VERBATIM: on `run_in_flight` +
        // `details.run_id`, call `service.clearLastError()` AND set
        // activeRunId, then start polling. Silent-adoption UX
        // (no error banner / no snack-bar) is asserted by the
        // 409-adoption spec. No hint is available on a 409 body;
        // the service defaults to the floor budget.
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

  private startPolling(runId: string, hintMs: number | null = null): void {
    this.pollSub?.unsubscribe();
    this.pollTeardownCount++;
    const sub = this.service.pollRun(runId, undefined, hintMs).subscribe({
      next: (run: CheckpointCleanupRun) => {
        this.lastExecuteResult.set(run);
        if (
          run.status === 'succeeded' ||
          run.status === 'failed' ||
          run.status === 'interrupted'
        ) {
          this.executing.set(false);
          this.activeRunId.set(null);
          // Mirror-vs-production ordering note (test-mirror only):
          // Production (member-ref `pollSub`) executes, in order:
          //   pollSub.unsubscribe() → pollSub = null → refreshStatus()
          //   → activeStep.set(3) (on `succeeded`)
          //   — see ts:608-624.
          // The mirror uses a local `sub` closure reference. A
          // synchronous `sub.unsubscribe()` during the next handler
          // closes the subscription mid-emission and aborts the
          // statements that follow in the same callback (RxJS
          // closed-during-emission edge case). To avoid that, the
          // mirror intentionally runs `activeStep.set(3)` BEFORE
          // `sub.unsubscribe()`. The mirror's local-ref ordering is
          // therefore: activeStep.set(3) → refreshStatus() →
          // sub.unsubscribe() — NOT identical to production's
          // member-ref ordering. Assertions in this file are
          // order-independent (no test asserts the relative order
          // of these three calls), so the divergence is safe.
          //
          // W1 — auto-advance to Step 4 (Result) on successful
          // execute. Mirrors production: succeeded only — failed
          // and interrupted stay on Step 3 with inline UI affordances.
          if (run.status === 'succeeded') {
            this.activeStep.set(3);
          }
          this.refreshStatus();
          sub.unsubscribe();
          sub.closed = true;
          this.pollTeardownCount++;
        }
      },
      error: () => {
        this.executing.set(false);
        this.activeRunId.set(null);
        sub.unsubscribe();
        sub.closed = true;
        this.pollTeardownCount++;
      },
    });
    this.pollSub = sub;
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.pollSub?.unsubscribe();
    if (this.pollSub) this.pollSub.closed = true;
    this.pollTeardownCount++;
  }

  isMaintenanceDisabled(): boolean {
    return this.lastError()?.error === 'maintenance_disabled';
  }

  dismissError(): void {
    this.service.clearLastError();
  }

  // Item 12 — `trackByThreadId` / `statusSnapshot` were dead
  // surface (template uses `track entry.thread_id` directly; the
  // status accessor is just `this.status()`). Mirrored deletion.
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

  // ── Wizard helpers (ck-redesign-2026q4) — mirror production verbatim.

  /** Step 1 → Step 2 gate (spec §2.3, AC-4). */
  canContinueFromStep1(): boolean {
    return this.canDryRun() && !this.isMaintenanceDisabled() && !this.isRunInFlight();
  }

  /** Step 2 → Step 3 gate (spec §2.4). */
  canContinueFromStep2(): boolean {
    const dry = this.lastDryRun();
    if (!dry) return false;
    if (this.dryRunning() || this.executing() || this.isRunInFlight()) return false;
    return (dry.would_delete_count ?? 0) > 0;
  }

  /** Step 3 action gate (spec §2.5). */
  canContinueFromStep3(): boolean {
    return !!this.lastDryRun() && !this.executing() && !this.isRunInFlight();
  }

  // Status Strip render helpers (spec §2.1).
  statusStripKeepN(): string {
    if (this.isMaintenanceDisabled() || !this.status()) return '—';
    const v = this.status()?.config?.checkpoint_max_per_thread;
    return typeof v === 'number' ? String(v) : '—';
  }
  statusStripLastRun(): string {
    if (this.isMaintenanceDisabled() || !this.status()) return '—';
    const last = this.status()?.last_run;
    if (!last) return 'Never run';
    const bytes =
      last.summary?.blobs?.bytes_freed ?? last.summary?.blobs?.would_free_bytes ?? 0;
    const time = this.formatTimestamp(last.completed_at);
    return `${last.status} · ${this.formatBytes(bytes)} · ${time}`;
  }
  statusStripDryRunFresh(): string {
    if (this.isMaintenanceDisabled() || !this.lastDryRun()) {
      return 'Stale — run dry-check';
    }
    const dry = this.lastDryRun()!;
    const scanned = dry.scanned?.thread_ns_pairs ?? 0;
    const time = this.formatTimestamp(dry.fresh_until);
    return `${scanned} pairs · expires in ${time}`;
  }

  // ── v3.2 — dry-run projection helpers ─────────────────────────────────

  /** R-1 — "—" for zero, else human-formatted bytes. This-run number. */
  dryRunProjectionNow(dryRun: CheckpointCleanupDryRun): string {
    const v = dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes ?? 0;
    return this.formatOrDash(v);
  }

  /** R-1 — "—" for zero, else human-formatted bytes. Follow-up run. */
  dryRunProjectionAfter(dryRun: CheckpointCleanupDryRun): string {
    const v = dryRun.bytes_reclaimable_after_row_prune ?? 0;
    return this.formatOrDash(v);
  }

  /** R-1 — "—" for zero, else human-formatted bytes. Sum. */
  dryRunProjectionTotal(dryRun: CheckpointCleanupDryRun): string {
    const now = dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes ?? 0;
    const after = dryRun.bytes_reclaimable_after_row_prune ?? 0;
    const v = dryRun.bytes_reclaimable_total ?? now + after;
    return this.formatOrDash(v);
  }

  /** Pure — "—" for zero/negative/non-finite; otherwise formatBytes. */
  private formatOrDash(v: number): string {
    return typeof v === 'number' && v > 0 && Number.isFinite(v) ? this.formatBytes(v) : '—';
  }

  /** R-4 — skip-flag honesty banner trigger. */
  dryRunSkippedHonestyActive(dryRun: CheckpointCleanupDryRun): boolean {
    return dryRun.skipped.length > 0;
  }

  /** Never-pruned profile — `now == 0 && after > 0`. */
  isNeverPrunedProfile(dryRun: CheckpointCleanupDryRun): boolean {
    const now = dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes ?? 0;
    const after = dryRun.bytes_reclaimable_after_row_prune ?? 0;
    return now === 0 && after > 0;
  }

  // ── v3.2 — post-run banner helpers (R-5) ──────────────────────────────

  /** Banner visibility — last execute succeeded, projection.after > 0,
   *  NOT yet hidden by a fresh converging dry-run. v3.2 B1 + B2: the
   *  hide rule is `(anchor === null || dry.run_id !== anchor) &&
   *  now == 0`. See production `showRunAgainBanner` JSDoc for the
   *  full truth-table derivation (rows a/b/c/d). */
  showRunAgainBanner(): boolean {
    const run = this.lastExecuteResult();
    if (!run || run.status !== 'succeeded' || !run.summary) return false;
    const after =
      run.summary.projection?.bytes_reclaimable_after_row_prune_at_dry_run ?? 0;
    if (after <= 0) return false;
    const dry = this.lastDryRun();
    const anchor = this.executedDryRunId();
    if (
      dry &&
      (anchor === null || dry.run_id !== anchor) &&
      (dry.bytes_reclaimable_now ?? 0) === 0
    ) {
      return false;
    }
    return true;
  }

  /** Banner CTA copy source — projection-after bytes. */
  runAgainReclaimBytes(): number {
    const run = this.lastExecuteResult();
    return run?.summary?.projection?.bytes_reclaimable_after_row_prune_at_dry_run ?? 0;
  }

  /**
   * Item 4 / Item 20 — error code → human label for the inline banner.
   * Curated mapping via `ERROR_LABEL_MAP` (table lookup); everything
   * else renders verbatim.
   *
   * Fix commission 2026-09-29 — the FE-only `'poll_stale'` sentinel
   * (mapped from `details.fe_synthesized_poll_timeout === true`) is
   * REMOVED. The service no longer synthesizes a poll-timeout
   * error; the post-cap backoff phase keeps polling until terminal.
   * Mirrors production.
   */
  private static readonly ERROR_LABEL_MAP: Partial<Record<string, string>> = {
    internal_error: 'Internal server error',
  };

  errorLabel(code: string): string {
    return TestableCheckpointCleanupComponent.ERROR_LABEL_MAP[code] ?? code;
  }

  /**
   * Item 4 — derive the FE display code from the wire body. With
   * the FE-synthesized poll-timeout marker removed (fix commission
   * 2026-09-29), the marker branch into `'poll_stale'` is gone.
   * Mirrors production verbatim — thin pass-through over
   * `lastError().error`.
   */
  displayErrorCode(): MaintenanceDisplayCode | '' {
    const err = this.lastError();
    return err ? err.error : '';
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
    // v3.2 — journey copy (amendment VERBATIM). Per-run honesty
    // (what THIS run does) + journey honesty (what a follow-up run
    // reclaims). Two `formatBytes()` calls (now, after) +
    // `would_delete.checkpoint_rows` + literal "running cleanup again".
    const now = this.formatBytes(dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes);
    const after = this.formatBytes(dryRun.bytes_reclaimable_after_row_prune ?? 0);
    const rows = dryRun.would_delete.checkpoint_rows;
    return (
      `This run will permanently delete ~${now} of unreferenced blobs ` +
      `and ${rows} excess checkpoint rows. After this run, ~${after} ` +
      `more becomes reclaimable by running cleanup again. This may ` +
      `take several minutes on large databases. This cannot be undone.`
    );
  }

  /** Item 20 — single-source the error-snackbar toast open() helper. */
  private snackError(message: string, duration: number = 5000): void {
    this.snackBar.open(message, 'Dismiss', {
      duration,
      panelClass: 'error-snackbar',
    });
  }

  private showSnackForLastError(): void {
    const err = this.lastError();
    if (!err) return;
    if (err.error === 'dry_run_stale' || err.error === 'dry_run_required') {
      this.lastDryRun.set(null);
      this.snackError('Re-run the dry-run check before executing.');
      return;
    }
    if (err.error === 'byte_count_mismatch') {
      this.lastDryRun.set(null);
      this.snackError('The dry-run result changed since you ran it — re-run and try again.');
      return;
    }
    if (err.error === 'not_found') {
      this.snackError(
        'The run record was not found — re-run from the status page.',
        6000,
      );
      return;
    }
    this.snackError(err.message ?? `Error: ${err.error}`);
  }
}

// ── Fixtures (Item 10) ──────────────────────────────────────────────────
// Canonical fixtures (DRY_RUN, STALE_DRY_RUN, STATUS, EXECUTE_RESP,
// BYTES_256_MB, AVAILABILITY_READY, BLOBS_DRY, BLOBS_DESTRUCTIVE,
// makeRun) live in ./__fixtures__/fixtures.ts. The component spec
// imports the same set as the service spec — drift between the two
// is no longer possible.

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
      //
      // N1 (merge-gate hardening) — the mock override MUST emulate
      // the service-layer `catchError` (`execute()` at
      // `checkpoint-cleanup.service.ts:172-176` calls
      // `this.lastError.set(body)` before re-throwing). Without this
      // emulation, `clearLastError()` could be deleted from the
      // mirror and `lastError()` would still be null (no one ever
      // set it) — making the assertion a dead guard. With
      // emulation, the assertion becomes a LIVE guard: deleting the
      // mirror's `clearLastError()` leaves `lastError` holding the
      // body and the test goes red.
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      const adoptedBody: MaintenanceErrorBody = {
        error: 'run_in_flight',
        details: { run_id: 'ckpt-already-running', started_at: '2026-09-27T03:21:30.456789+00:00' },
      };
      // Override the mock's execute to mirror service `catchError`
      // (set `lastError` THEN throw) so the adoption-pin assertion
      // is live.
      service.execute = () => {
        service.lastError.set(adoptedBody);
        return throwError(() => adoptedBody);
      };
      component.onExecute();
      expect(service.pollRunCalls).toHaveLength(1);
      expect(service.pollRunCalls[0].runId).toBe('ckpt-already-running');
      expect(MockSnackBarRef.openCalls).toHaveLength(0); // no error toast
      // Item 1 pin: silent-adoption UX — lastError MUST be null after
      // adoption so the inline banner does not render during the
      // "ride along" polling. Live guard (see N1 note above).
      expect(component.lastError()).toBeNull();
    });

    it('on 409 run_in_flight WITHOUT details.run_id: error banner surfaces (fallthrough)', () => {
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      // N1 — same emulation as the adoption test above: the
      // service-layer `catchError` sets `lastError` before throwing.
      // The fallthrough component path (no `details.run_id` → no
      // adoption) leaves `lastError` populated so the inline
      // banner renders; mirroring the production wire shape.
      const fallthroughBody: MaintenanceErrorBody = {
        error: 'run_in_flight',
        // no details.run_id
      };
      service.execute = () => {
        service.lastError.set(fallthroughBody);
        return throwError(() => fallthroughBody);
      };
      component.onExecute();
      expect(service.pollRunCalls).toHaveLength(0); // no adoption
      expect(component.executing()).toBe(false); // not stuck
      expect(component.lastError()?.error).toBe('run_in_flight'); // banner surfaces
    });
  });

  describe('display sentinel (Item 4 — verbatim pass-through, fix commission 2026-09-29)', () => {
    // The OLD contract branched `details.fe_synthesized_poll_timeout === true`
    // into the FE-only `'poll_stale'` sentinel. The NEW contract
    // (post-cap backoff phase, no synthesized poll-timeout) drops the
    // marker branch — `displayErrorCode()` is a thin pass-through over
    // `lastError().error`. Iter3: the dead `'poll_stale'` member was
    // dropped from the `MaintenanceDisplayCode` type too — no
    // producer, no consumer.

    it('passes BE-said error codes through verbatim (e.g. internal_error)', () => {
      service.lastError.set({ error: 'internal_error', message: 'boom' });
      expect(component.displayErrorCode()).toBe('internal_error');
      expect(component.errorLabel(component.displayErrorCode())).toBe('Internal server error');
    });

    it('passes a wire not_initialized body verbatim (no FE poll-timeout branch in the new contract)', () => {
      // A genuine BE `not_initialized` (e.g. backend unavailable)
      // surfaces verbatim. The OLD marker-based branch into
      // `'poll_stale'` is gone; even a body carrying
      // `fe_synthesized_poll_timeout: true` (legacy or BE-side
      // artifact) passes through verbatim — the FE no longer
      // recognizes the marker.
      service.lastError.set({ error: 'not_initialized', message: 'backend not ready' });
      expect(component.displayErrorCode()).toBe('not_initialized');
    });

    it('returns empty string when lastError is null (no banner)', () => {
      service.lastError.set(null);
      expect(component.displayErrorCode()).toBe('');
    });

    it('returns empty string when a stale poll-timeout body lingers (marker ignored)', () => {
      // Defensive regression: a legacy caller could (in principle)
      // set a `fe_synthesized_poll_timeout` body on `lastError`.
      // The NEW display code ignores the marker and surfaces the
      // wire `error` verbatim — never the FE-only sentinel.
      service.lastError.set({
        error: 'not_initialized',
        message: 'stale',
        details: { fe_synthesized_poll_timeout: true },
      });
      expect(component.displayErrorCode()).toBe('not_initialized');
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

  describe('pollRun() hint plumbing (fix commission 2026-09-29)', () => {
    it('performExecute plumbs the 202 body’s expected_duration_ms_hint into pollRun() as the 3rd arg', () => {
      // The mock returns `expected_duration_ms_hint: 412` on execute.
      // The new contract sizes the active poll budget as
      // `max(hint × 2, 15 min)`; the component must pass the
      // hint through. Without plumbing, the service defaults to
      // the floor budget — a smaller hint than the BE advertised.
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      component.onExecute();
      expect(service.pollRunCalls).toHaveLength(1);
      expect(service.pollRunCalls[0].runId).toBe('ckpt-test-execute-1');
      expect(service.pollRunCalls[0].hintMs).toBe(412);
    });
  });

  describe('re-entry resume (fix commission 2026-09-29)', () => {
    // Page-refresh-mid-poll must recover into live tracking.
    // The BE's `/status` body carries `in_flight.run_id` while a
    // run is non-terminal; the component's refreshStatus() must
    // start polling it without waiting for the user to click
    // anything.
    //
    // Test mock strategy: `pollRun` returns a non-terminal
    // `running` run so the terminal-handler doesn't fire its
    // post-terminal `refreshStatus()` (which would re-enter the
    // polling loop against the still-in_flight status body). The
    // contract under test is the re-entry kick-off, not the
    // terminal-stop semantics (those are covered by the existing
    // 9-test "execute confirm flow — polls to terminal" e2e +
    // the service spec's terminal-stop describe block).

    it('refreshStatus() resumes polling against status.in_flight.run_id', () => {
      const inFlightRunId = 'ckpt-refreshed-mid-poll-1';
      const inFlightBody = {
        run_id: inFlightRunId,
        kind: 'manual_execute' as const,
        started_at: new Date().toISOString(),
        triggered_by: 'user',
      };
      const statusWithInFlight: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: null,
        in_flight: inFlightBody,
      };
      // Override fetchStatus + pollRun to use non-terminal
      // responses — sidesteps the mock's auto-succeed that would
      // otherwise trigger the post-terminal refreshStatus loop.
      service.status.set(statusWithInFlight);
      service.fetchStatus = () => {
        service.fetchStatusCalls++;
        return of(statusWithInFlight);
      };
      service.pollRun = (runId: string, intervalMs?: number, hintMs?: number | null) => {
        service.pollRunCalls.push({ runId, intervalMs, hintMs });
        return of({
          run_id: runId,
          kind: 'manual_execute' as const,
          status: 'running' as const,
          started_at: new Date().toISOString(),
          completed_at: null,
          summary: null,
          error: null,
        });
      };
      component.refreshStatus();
      expect(service.pollRunCalls).toHaveLength(1);
      expect(service.pollRunCalls[0].runId).toBe(inFlightRunId);
      // No hint on re-entry — `startPolling(inFlight.run_id)` is
      // called with `hintMs` undefined → service receives `null`
      // (the default in `pollRun(runId, intervalMs, hintMs = null)`).
      expect(service.pollRunCalls[0].hintMs).toBeNull();
      expect(component.activeRunId()).toBe(inFlightRunId);
    });

    it('refreshStatus() is idempotent: a second refresh with the same in_flight does NOT double-poll', () => {
      const inFlightRunId = 'ckpt-refreshed-mid-poll-2';
      const statusWithInFlight: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: null,
        in_flight: {
          run_id: inFlightRunId,
          kind: 'manual_execute' as const,
          started_at: new Date().toISOString(),
          triggered_by: 'user',
        },
      };
      service.status.set(statusWithInFlight);
      service.fetchStatus = () => {
        service.fetchStatusCalls++;
        return of(statusWithInFlight);
      };
      service.pollRun = (runId: string, intervalMs?: number, hintMs?: number | null) => {
        service.pollRunCalls.push({ runId, intervalMs, hintMs });
        return of({
          run_id: runId,
          kind: 'manual_execute' as const,
          status: 'running' as const,
          started_at: new Date().toISOString(),
          completed_at: null,
          summary: null,
          error: null,
        });
      };
      component.refreshStatus();
      component.refreshStatus();
      // Exactly one poll subscription, not two — the idempotency
      // guard in `resumePollingIfInFlight` short-circuits when
      // `activeRunId` already matches and the prior sub is live.
      expect(service.pollRunCalls).toHaveLength(1);
    });

    it('refreshStatus() does NOT poll when status.in_flight is null', () => {
      // No in-flight run → no auto-resume; the existing
      // last_run banner-seed branch is the only side effect.
      service.status.set({ ...STATUS, last_run: null, in_flight: null });
      component.refreshStatus();
      expect(service.pollRunCalls).toHaveLength(0);
    });

    // Iter2 review finding 2 — feedback-loop guard. Without this
    // guard, a BE that transiently retains `in_flight` after the
    // run reached terminal would cause the terminal handler to
    // fire, null `activeRunId`, call `refreshStatus()`, see
    // `in_flight` still populated, start a NEW poll, observe the
    // terminal value, fire the terminal handler again → loop.
    it('refreshStatus() does NOT poll when lastExecuteResult already carries a terminal row for the same run_id (iter2 feedback-loop guard)', () => {
      const inFlightRunId = 'ckpt-feedback-loop-guard-1';
      const statusWithInFlight: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: null,
        in_flight: {
          run_id: inFlightRunId,
          kind: 'manual_execute' as const,
          started_at: new Date().toISOString(),
          triggered_by: 'user',
        },
      };
      service.status.set(statusWithInFlight);
      service.fetchStatus = () => {
        service.fetchStatusCalls++;
        return of(statusWithInFlight);
      };
      // Pre-seed `lastExecuteResult` with a terminal row for the
      // SAME run_id — this models the post-terminal state where
      // the terminal handler has already fired for this id but
      // the BE's `in_flight` slot has not yet been cleared. The
      // guard MUST suppress the resume in this case.
      component.lastExecuteResult.set({
        run_id: inFlightRunId,
        kind: 'manual_execute' as const,
        status: 'succeeded' as const,
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        summary: null,
        error: null,
      });
      // activeRunId is null (the terminal handler nulled it
      // before the post-terminal refreshStatus fired) — the
      // activeRunId guard does NOT short-circuit, so this case
      // reaches the new lastExecuteResult guard.
      component.activeRunId.set(null);
      const beforeCount = service.pollRunCalls.length;
      component.refreshStatus();
      // No new poll subscription — guard suppresses the resume.
      expect(service.pollRunCalls).toHaveLength(beforeCount);
      expect(component.activeRunId()).toBeNull();
    });

    it('refreshStatus() DOES poll when lastExecuteResult is for a DIFFERENT run_id (guard is per-id, not global)', () => {
      // The pre-seeded terminal row is for a DIFFERENT run_id
      // (a prior run that completed earlier in the same session).
      // The guard is per-id; this id mismatch MUST NOT block the
      // resume for the new in-flight id.
      //
      // Override `pollRun` to return non-terminal RUN_RUNNING so
      // the mock's default immediate-SUCCEEDED doesn't fire the
      // terminal handler before the assertions can verify the
      // resume kicked off (matches the pattern in the sibling
      // re-entry tests above).
      const inFlightRunId = 'ckpt-different-run-id-1';
      const previousRunId = 'ckpt-previous-terminal-id';
      const statusWithInFlight: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: null,
        in_flight: {
          run_id: inFlightRunId,
          kind: 'manual_execute' as const,
          started_at: new Date().toISOString(),
          triggered_by: 'user',
        },
      };
      service.status.set(statusWithInFlight);
      service.fetchStatus = () => {
        service.fetchStatusCalls++;
        return of(statusWithInFlight);
      };
      service.pollRun = (runId: string, intervalMs?: number, hintMs?: number | null) => {
        service.pollRunCalls.push({ runId, intervalMs, hintMs });
        return of({
          run_id: runId,
          kind: 'manual_execute' as const,
          status: 'running' as const,
          started_at: new Date().toISOString(),
          completed_at: null,
          summary: null,
          error: null,
        });
      };
      component.lastExecuteResult.set({
        run_id: previousRunId,
        kind: 'manual_execute' as const,
        status: 'succeeded' as const,
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        summary: null,
        error: null,
      });
      component.activeRunId.set(null);
      const pollsBefore = service.pollRunCalls.length;
      component.refreshStatus();
      // Resume proceeds — guard does not block this id.
      expect(service.pollRunCalls.length).toBe(pollsBefore + 1);
      expect(service.pollRunCalls[service.pollRunCalls.length - 1].runId).toBe(
        inFlightRunId,
      );
      expect(component.activeRunId()).toBe(inFlightRunId);
    });

    it('refreshStatus() DOES poll when lastExecuteResult is for the same run_id but NON-terminal (no false suppression)', () => {
      // Defensive pin: the guard fires ONLY on a terminal
      // lastExecuteResult. A running pre-seed must NOT suppress
      // the resume (that would break the legitimate re-entry
      // case where two refreshStatus() calls race before the
      // first poll's terminal emission lands in lastExecuteResult).
      //
      // Override `pollRun` to return non-terminal RUN_RUNNING
      // (sibling pattern; see the in_flight re-entry tests above).
      const inFlightRunId = 'ckpt-guard-running-preexists';
      const statusWithInFlight: CheckpointCleanupStatus = {
        ...STATUS,
        last_run: null,
        in_flight: {
          run_id: inFlightRunId,
          kind: 'manual_execute' as const,
          started_at: new Date().toISOString(),
          triggered_by: 'user',
        },
      };
      service.status.set(statusWithInFlight);
      service.fetchStatus = () => {
        service.fetchStatusCalls++;
        return of(statusWithInFlight);
      };
      service.pollRun = (runId: string, intervalMs?: number, hintMs?: number | null) => {
        service.pollRunCalls.push({ runId, intervalMs, hintMs });
        return of({
          run_id: runId,
          kind: 'manual_execute' as const,
          status: 'running' as const,
          started_at: new Date().toISOString(),
          completed_at: null,
          summary: null,
          error: null,
        });
      };
      component.lastExecuteResult.set({
        run_id: inFlightRunId,
        kind: 'manual_execute' as const,
        // status: 'running' (or any other non-terminal value) →
        // guard must NOT fire.
        status: 'running' as const,
        started_at: new Date().toISOString(),
        completed_at: null,
        summary: null,
        error: null,
      });
      component.activeRunId.set(null);
      const pollsBefore = service.pollRunCalls.length;
      component.refreshStatus();
      expect(service.pollRunCalls.length).toBe(pollsBefore + 1);
      expect(service.pollRunCalls[service.pollRunCalls.length - 1].runId).toBe(
        inFlightRunId,
      );
      expect(component.activeRunId()).toBe(inFlightRunId);
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

  // ── v3.2 — Pin 16: projection-fields-render ─────────────────────────

  describe('projection-fields-render (Pin 16 — v3.2, R-1/R-4)', () => {
    it('never-pruned scenario: now == "—", after ≈ 11 GB, total ≈ 11 GB', () => {
      expect(component.dryRunProjectionNow(DRY_RUN_NEVER_PRUNED)).toBe('—');
      const after = component.dryRunProjectionAfter(DRY_RUN_NEVER_PRUNED);
      // formatBytes is binary (1024); 11,811,060,000 B → "11.0 GB"
      expect(after).toMatch(/^11(\.\d+)? GB/);
      const total = component.dryRunProjectionTotal(DRY_RUN_NEVER_PRUNED);
      expect(total).toMatch(/^11(\.\d+)? GB/);
    });

    it('fully-pruned scenario: now = 256 MB, after = "—", total = 256 MB', () => {
      expect(component.dryRunProjectionNow(DRY_RUN_PRUNED)).toBe('256 MB');
      expect(component.dryRunProjectionAfter(DRY_RUN_PRUNED)).toBe('—');
      expect(component.dryRunProjectionTotal(DRY_RUN_PRUNED)).toBe('256 MB');
    });

    it('mixed projection: no "—" substitutions, every number formatted', () => {
      expect(component.dryRunProjectionNow(DRY_RUN_MIXED)).toBe('100 MB');
      expect(component.dryRunProjectionAfter(DRY_RUN_MIXED)).toBe('5.0 GB');
      expect(component.dryRunProjectionTotal(DRY_RUN_MIXED)).toBe('5.1 GB');
    });

    it('sentinel "—" fires for zero, negative, non-finite inputs', () => {
      const zeroed: CheckpointCleanupDryRun = {
        ...DRY_RUN_PRUNED,
        bytes_reclaimable_now: 0,
      };
      expect(component.dryRunProjectionNow(zeroed)).toBe('—');
      const negative: CheckpointCleanupDryRun = {
        ...DRY_RUN,
        bytes_reclaimable_now: -1,
      };
      expect(component.dryRunProjectionNow(negative)).toBe('—');
      const naned: CheckpointCleanupDryRun = {
        ...DRY_RUN,
        bytes_reclaimable_now: NaN,
      };
      expect(component.dryRunProjectionNow(naned)).toBe('—');
    });

    it('falls back to would_free_bytes when bytes_reclaimable_now is missing (v3.1 payload defensive)', () => {
      // Legacy v3.1 dry-run lacks the projection fields. The "now"
      // helper MUST render via `would_free_bytes` (shape-stability
      // alias — they MUST render the same number per AM-3).
      const legacy: CheckpointCleanupDryRun = { ...DRY_RUN };
      expect(component.dryRunProjectionNow(legacy)).toBe('256 MB');
      // total reads would_free_bytes (sum of legacy-fallback now + 0 after)
      expect(component.dryRunProjectionTotal(legacy)).toBe('256 MB');
      // after is zero, "—"
      expect(component.dryRunProjectionAfter(legacy)).toBe('—');
    });

    it('total derives now+after when bytes_reclaimable_total absent (wire summary drift tolerance)', () => {
      const drifted: CheckpointCleanupDryRun = {
        ...DRY_RUN_NEVER_PRUNED,
        bytes_reclaimable_total: undefined,
      };
      const total = component.dryRunProjectionTotal(drifted);
      // equals after (since now = 0)
      expect(total).toBe(component.dryRunProjectionAfter(drifted));
    });

    it('skip-flag honesty active iff skipped[] non-empty (R-4)', () => {
      expect(component.dryRunSkippedHonestyActive(DRY_RUN_NEVER_PRUNED)).toBe(false);
      expect(component.dryRunSkippedHonestyActive(DRY_RUN_WITH_SKIPPED)).toBe(true);
      // DRY_RUN has skipped: [3 entries] — existing fixture carries it
      expect(component.dryRunSkippedHonestyActive(DRY_RUN)).toBe(true);
    });

    it('isNeverPrunedProfile: now == 0 && after > 0 — only the never-pruned case', () => {
      expect(component.isNeverPrunedProfile(DRY_RUN_NEVER_PRUNED)).toBe(true);
      expect(component.isNeverPrunedProfile(DRY_RUN_PRUNED)).toBe(false);
      expect(component.isNeverPrunedProfile(DRY_RUN_MIXED)).toBe(false);
      // both zero → not a never-pruned profile
      const bothZero: CheckpointCleanupDryRun = {
        ...DRY_RUN_NEVER_PRUNED,
        bytes_reclaimable_after_row_prune: 0,
      };
      expect(component.isNeverPrunedProfile(bothZero)).toBe(false);
    });
  });

  // ── v3.2 — Pin 17: confirm-message-journey-copy ──────────────────────

  describe('confirm-message-journey-copy (Pin 17 — v3.2)', () => {
    /** Builds the message without opening the dialog, then asserts
     *  on it directly. The component's `buildConfirmMessage` is
     *  private; we exercise via the public dialog `data.message`. */
    function captureConfirmMessage(dryRun: CheckpointCleanupDryRun): string {
      service.lastDryRun.set(dryRun);
      mockDialog.nextResult = false; // cancel — no execute path
      mockDialog.openCalls = [];
      component.onExecute();
      const captured = mockDialog.openCalls[0]?.data as
        | { message: string }
        | undefined;
      if (!captured) {
        throw new Error('confirm dialog was not opened');
      }
      return captured.message;
    }

    it('journey copy extends Pin 5 — now + rows + fmt(after) + "running cleanup again"', () => {
      const msg = captureConfirmMessage(DRY_RUN_NEVER_PRUNED);
      // fmt(now) — 0 → "0 B" in formatBytes (NOT "—"; the dialog
      //  promises what THIS run deletes, even when 0, so the user
      //  is not misled into thinking it's "skip"; spec uses
      //  formatBytes unconditionally for the consent instrument).
      expect(msg).toContain('0 B');
      // rows — 104,501 excess checkpoint rows
      expect(msg).toContain('104501');
      // fmt(after) — 11,811,060,000 bytes → "11.0 GB" (binary)
      expect(msg).toMatch(/11(\.\d+)? GB/);
      // Journey anchor literal
      expect(msg).toMatch(/running cleanup again/i);
    });

    it('AM-16 honest-duration copy preserved', () => {
      const msg = captureConfirmMessage(DRY_RUN);
      expect(msg).toMatch(/several minutes/i);
      expect(msg).toMatch(/cannot be undone/i);
      expect(msg).toContain('256 MB');
      expect(msg).toContain('2'); // 2 excess checkpoint rows (DRY_RUN fixture)
    });

    it('destructive phrasing present (extends AM-17)', () => {
      const msg = captureConfirmMessage(DRY_RUN);
      // v3.2 wording — "This run will permanently delete"
      expect(msg).toContain('This run will permanently delete');
      expect(msg).toContain('cannot be undone');
    });

    it('path present on mixed projection (no zero fallback)', () => {
      const msg = captureConfirmMessage(DRY_RUN_MIXED);
      expect(msg).toContain('100 MB'); // fmt(now)
      expect(msg).toMatch(/5\.0 GB/); // fmt(after)
      expect(msg).toContain('5000'); // 5000 excess rows
    });
  });

  // ── v3.2 — Pin 18: run-again-banner-when-projection-nonzero ───────────

  describe('run-again-banner-when-projection-nonzero (Pin 18 — v3.2, R-5)', () => {
    it('banner VISIBLE when last execute succeeded AND projection.after > 0', () => {
      component.lastExecuteResult.set(RUN_SUCCEEDED_NEVER_PRUNED);
      expect(component.showRunAgainBanner()).toBe(true);
      expect(component.runAgainReclaimBytes()).toBe(11811060000);
    });

    it('banner HIDDEN when last execute status is failed or interrupted', () => {
      component.lastExecuteResult.set(RUN_FAILED_NEVER_PRUNED);
      expect(component.showRunAgainBanner()).toBe(false);
      component.lastExecuteResult.set(RUN_INTERRUPTED_NEVER_PRUNED);
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('banner HIDDEN when projection.after is 0 (no follow-up reclaimable)', () => {
      component.lastExecuteResult.set(RUN_SUCCEEDED_PRUNED);
      expect(component.showRunAgainBanner()).toBe(false);
      expect(component.runAgainReclaimBytes()).toBe(0);
    });

    it('banner HIDDEN when summary has no projection block (auto rows, R-5)', () => {
      component.lastExecuteResult.set(RUN_AUTO_NO_PROJECTION);
      expect(component.showRunAgainBanner()).toBe(false);
      // auto rows still expose 0 for the reclaim bytes
      expect(component.runAgainReclaimBytes()).toBe(0);
    });

    it('banner HIDDEN when there is no last execute result', () => {
      component.lastExecuteResult.set(null);
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('banner HIDDEN once a fresh dry-run converges (bytes_reclaimable_now == 0)', () => {
      component.lastExecuteResult.set(RUN_SUCCEEDED_NEVER_PRUNED);
      // v3.2 B1 — anchor the hide rule to the dry-run that backed
      // the execute. Without this, the hide rule fires on the
      // pre-execute dry-run's `now == 0` and the banner never
      // appears on never-pruned profiles.
      component.executedDryRunId.set(DRY_RUN_NEVER_PRUNED.run_id);
      // After > 0 case: dry-run shows now > 0 (mixed / pruned) — banner visible
      service.lastDryRun.set(DRY_RUN_PRUNED);
      expect(component.showRunAgainBanner()).toBe(true);
      // After a fresh dry-run converges (now == 0) — banner hides
      const converged: CheckpointCleanupDryRun = {
        ...DRY_RUN_PRUNED,
        bytes_reclaimable_now: 0,
      };
      service.lastDryRun.set(converged);
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('banner CTA starts a NEW dry-run (NEVER a silent execute) — covers the (click)=onDryRun binding', () => {
      // The Pin 18 source-grep asserts `(click)="onDryRun()"` on
      // the button (template). The behavioural counterpart here
      // proves the production entry point never calls execute()
      // when the user clicks "Run again".
      component.lastExecuteResult.set(RUN_SUCCEEDED_NEVER_PRUNED);
      service.lastDryRun.set(DRY_RUN_NEVER_PRUNED);
      service.dryRunCalls = 0;
      service.executeCalls = [];
      component.onDryRun();
      expect(service.dryRunCalls).toBe(1);
      expect(service.executeCalls).toHaveLength(0);
    });

    // ── v3.2 B1 — never-pruned profile banner journey ──────────────────

    it('DRY_RUN_NEVER_PRUNED — never-pruned profile: pre-execute dry-run now==0 + execute succeeded → banner VISIBLE (B1 incident scenario)', () => {
      // The pre-execute dry-run on a never-pruned DB reports now=0
      // (pass 1 frees nothing; Op D orphans blobs for pass 2). Under
      // the pre-B1 hide rule, `now == 0` would hide the banner on
      // this SAME dry-run — the incident scenario. Under B1 the
      // hide rule is scoped by `executedDryRunId` (which matches
      // `lastDryRun.run_id` here) so the banner stays visible
      // until the user runs a FRESH dry-run that converges.
      component.lastExecuteResult.set(RUN_SUCCEEDED_NEVER_PRUNED);
      component.executedDryRunId.set(DRY_RUN_NEVER_PRUNED.run_id);
      service.lastDryRun.set(DRY_RUN_NEVER_PRUNED);
      expect(component.showRunAgainBanner()).toBe(true);
      expect(component.runAgainReclaimBytes()).toBe(11811060000);
    });

    it('RUN_SUCCEEDED_NEVER_PRUNED — succeeded execute + projection.after > 0 + same dry-run now==0 (pre-hiding-rule scope) → banner VISIBLE (B1)', () => {
      // Mirror of the prior test emphasizing the post-execute /
      // pre-hiding-rule scope: the dry-run that was confirmed
      // against reports `now == 0`, the execute succeeded, the
      // banner must STILL be visible (the B1 anchor excludes the
      // matching `run_id` from the hide rule).
      component.lastExecuteResult.set(RUN_SUCCEEDED_NEVER_PRUNED);
      component.executedDryRunId.set(DRY_RUN_NEVER_PRUNED.run_id);
      // lastDryRun may be null at this point (e.g. after a fresh
      // page load mid-poll — B2 seeding path). Banner still visible.
      service.lastDryRun.set(null);
      expect(component.showRunAgainBanner()).toBe(true);
      expect(component.runAgainReclaimBytes()).toBe(11811060000);
    });

    // ── v3.2 B2 — banner refresh persistence ─────────────────────────

    it('B2 — page load with succeeded last_run + projection → banner VISIBLE without client action', () => {
      // Page load / refresh mid-/post-execute: the wire
      // `status.last_run` row carries the run-row's projection
      // echo (R-5). `refreshStatus()` seeds `lastExecuteResult`
      // from it, so the banner appears without the user taking
      // any action. Mirrors the seeded-from-server persistence
      // behavior called out in the v3.2 banner docstring.
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
      });
      component.refreshStatus();
      expect(component.lastExecuteResult()).not.toBeNull();
      expect(component.showRunAgainBanner()).toBe(true);
      expect(component.runAgainReclaimBytes()).toBe(11811060000);
    });

    it('B2 — last_run kind is auto → banner NOT seeded (no projection block on auto rows, R-5)', () => {
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_AUTO_SUCCEEDED,
      });
      component.refreshStatus();
      expect(component.lastExecuteResult()).toBeNull();
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('B2 — last_run projection.after is 0 → banner NOT seeded (nothing to reclaim)', () => {
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_SUCCEEDED_AFTER_ZERO,
      });
      component.refreshStatus();
      expect(component.lastExecuteResult()).toBeNull();
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('B2 — last_run status is failed → banner NOT seeded (banner is success-only)', () => {
      service.status.set({
        ...STATUS,
        last_run: {
          ...STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
          status: 'failed',
        },
      });
      component.refreshStatus();
      expect(component.lastExecuteResult()).toBeNull();
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('B2 — last_run null → banner NOT seeded (no prior run, no seeding)', () => {
      service.status.set({ ...STATUS, last_run: null });
      component.refreshStatus();
      expect(component.lastExecuteResult()).toBeNull();
      expect(component.showRunAgainBanner()).toBe(false);
    });

    // ── v3.2 B1 follow-up — chained production-sequence truth-table ────
    //
    // These drive the production sequence through the testable
    // component's METHODS (not hand-set state) so the seed→anchor
    // interaction is exercised end-to-end. The earlier hand-set
    // tests cannot catch the bug class where `seedBannerFromStatus`
    // wipes `executedDryRunId` (the original commit's wipe made the
    // hide rule dead in production).

    it('chained row (a): performExecute → terminal refreshStatus → same pre-execute dry-run (now==0) → banner VISIBLE', () => {
      // Production flow on a never-pruned profile:
      //   1. lastDryRun = DRY_RUN_NEVER_PRUNED (pre-execute dry-run, now==0)
      //   2. user clicks Execute → onExecute → performExecute sets
      //      executedDryRunId = dryRun.run_id (anchor)
      //   3. mock poll returns `succeeded` → startPolling terminal
      //      handler → refreshStatus()
      //   4. refreshStatus next handler → seedBannerFromStatus
      //      (must NOT wipe the anchor — B1 follow-up fix)
      //   5. assert: lastExecuteResult seeded, anchor preserved,
      //      lastDryRun unchanged, banner VISIBLE.
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
      });
      service.lastDryRun.set(DRY_RUN_NEVER_PRUNED);
      mockDialog.nextResult = true; // confirm execute

      component.onExecute();

      // Production sequence complete. State now:
      //   lastExecuteResult: seeded from status.last_run (overrides
      //     the poll result, both share the same shape)
      //   executedDryRunId: DRY_RUN_NEVER_PRUNED.run_id (anchor preserved)
      //   lastDryRun: DRY_RUN_NEVER_PRUNED (unchanged by execute)
      expect(component.lastExecuteResult()).not.toBeNull();
      expect(component.executedDryRunId()).toBe(DRY_RUN_NEVER_PRUNED.run_id);
      expect(component.lastDryRun()).toBe(DRY_RUN_NEVER_PRUNED);
      // Row (a) — pre-execute dry-run on never-pruned profile reports
      // now==0; without the B1 anchor scope the banner would never
      // appear (the incident scenario). With B1 the anchor excludes
      // the same run_id → banner VISIBLE.
      expect(component.showRunAgainBanner()).toBe(true);
    });

    it('chained row (b): performExecute → terminal refreshStatus → FRESH converging dry-run → banner HIDDEN', () => {
      // Same chain as row (a), then the user runs a fresh dry-run
      // (e.g. clicks "Run again" on the banner CTA). The fresh
      // dry-run has a NEW run_id (not the anchor) and converges
      // (now==0). The widened hide rule fires → banner HIDDEN.
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
      });
      service.lastDryRun.set(DRY_RUN_NEVER_PRUNED);
      mockDialog.nextResult = true;

      component.onExecute();

      // Drive a fresh dry-run that converges (different run_id).
      const freshConverging: CheckpointCleanupDryRun = {
        ...DRY_RUN_NEVER_PRUNED,
        run_id: 'ckpt-20260928_chained-b-fresh-converging-aa11bb22',
        bytes_reclaimable_now: 0,
      };
      service.lastDryRun.set(freshConverging);

      // Anchor is preserved (B1 follow-up); dry.run_id !== anchor;
      // now == 0 → widened hide rule fires → banner HIDDEN.
      expect(component.executedDryRunId()).toBe(DRY_RUN_NEVER_PRUNED.run_id);
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('chained row (d): page-load seed only (no execute this session) → fresh converging dry-run → banner HIDDEN', () => {
      // Seeded session path: refreshStatus fires the seed (sets
      // lastExecuteResult, leaves anchor null because no
      // performExecute this session). The user then runs a fresh
      // dry-run that converges. The widened hide rule fires via
      // the `anchor === null` arm → banner HIDDEN.
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
      });
      // Drive the seed via refreshStatus (no execute this session).
      component.refreshStatus();

      // Anchor stays null — the seed does NOT wipe it because the
      // seed does not touch it at all (B1 follow-up).
      expect(component.lastExecuteResult()).not.toBeNull();
      expect(component.executedDryRunId()).toBeNull();

      // Drive a fresh dry-run that converges.
      const freshConverging: CheckpointCleanupDryRun = {
        ...DRY_RUN_NEVER_PRUNED,
        run_id: 'ckpt-20260928_chained-d-seeded-fresh-cc33dd44',
        bytes_reclaimable_now: 0,
      };
      service.lastDryRun.set(freshConverging);

      // Widened rule: anchor === null → hide arm fires → banner HIDDEN.
      expect(component.showRunAgainBanner()).toBe(false);
    });

    it('chained row (a) negative: if seedBannerFromStatus WIPED the anchor, row (a) would HIDE — pins the regression class', () => {
      // This test asserts the WIDE-RULE behavior under the wipe:
      // simulating the pre-fix bug class by manually wiping the
      // anchor after performExecute (the wipe that
      // seedBannerFromStatus used to do). With the WIDENED hide
      // rule in place, a wiped anchor + pre-execute dry-run with
      // now==0 → hide fires → banner HIDDEN → row (a) REGRESSES.
      // This pins why BOTH parts of the fix are needed: just
      // widening the rule without removing the wipe would
      // regress row (a) on never-pruned profiles.
      service.status.set({
        ...STATUS,
        last_run: STATUS_LAST_RUN_SUCCEEDED_NEVER_PRUNED,
      });
      service.lastDryRun.set(DRY_RUN_NEVER_PRUNED);
      mockDialog.nextResult = true;
      component.onExecute();

      // Simulate the bug class: wipe the anchor after the chain.
      // Under the widened rule, this Hides the banner — row (a)
      // regresses.
      component.executedDryRunId.set(null);
      expect(component.showRunAgainBanner()).toBe(false);
    });
  });
});

// ── ck-redesign-2026q4 — wizard navigation, gating, Status Strip ───────

describe('CheckpointCleanupComponent — wizard (ck-redesign-2026q4)', () => {
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

  describe('activeStep initial value (AC-9)', () => {
    it('starts at 0 (Review)', () => {
      expect(component.activeStep()).toBe(0);
    });
  });

  describe('stepperNav helpers (AC-9)', () => {
    it('next() advances activeStep by 1, clamped at 3', () => {
      component.stepperNav().next();
      expect(component.activeStep()).toBe(1);
      component.stepperNav().next();
      component.stepperNav().next();
      component.stepperNav().next();
      expect(component.activeStep()).toBe(3);
      // Clamped at 3 — no rollover past Result.
      component.stepperNav().next();
      expect(component.activeStep()).toBe(3);
    });

    it('back() decrements activeStep by 1, clamped at 0', () => {
      component.activeStep.set(3);
      component.stepperNav().back();
      expect(component.activeStep()).toBe(2);
      component.stepperNav().back();
      component.stepperNav().back();
      component.stepperNav().back();
      expect(component.activeStep()).toBe(0);
      // Clamped at 0 — no negative roll.
      component.stepperNav().back();
      expect(component.activeStep()).toBe(0);
    });

    it('backToStart() resets activeStep to 0', () => {
      component.activeStep.set(3);
      component.stepperNav().backToStart();
      expect(component.activeStep()).toBe(0);
    });
  });

  describe('isDesktop signal (C1 — responsive orientation, AC-2)', () => {
    it('defaults to false (vertical orientation; matches narrow <1024px branch)', () => {
      expect(component.isDesktop()).toBe(false);
    });

    it('can be flipped to true to model the horizontal-orientation branch', () => {
      component.isDesktop.set(true);
      expect(component.isDesktop()).toBe(true);
    });
  });

  describe('onStepperSelectionChange (W1 — stepper navigation desync)', () => {
    it('updates activeStep() from the stepper selectedIndex (header-driven jump)', () => {
      // Simulate the operator clicking the "Result" step header
      // (index 3). The custom footer reads `activeStep()` to
      // render the right per-step buttons; without this sync the
      // footer would stay stuck on the previous step's buttons.
      component.onStepperSelectionChange({ selectedIndex: 3 });
      expect(component.activeStep()).toBe(3);
    });

    it('updates activeStep() from an arbitrary valid index', () => {
      component.onStepperSelectionChange({ selectedIndex: 1 });
      expect(component.activeStep()).toBe(1);
      component.onStepperSelectionChange({ selectedIndex: 2 });
      expect(component.activeStep()).toBe(2);
      component.onStepperSelectionChange({ selectedIndex: 0 });
      expect(component.activeStep()).toBe(0);
    });

    it('is a no-op when the stepper arg is undefined', () => {
      component.activeStep.set(2);
      component.onStepperSelectionChange(undefined);
      expect(component.activeStep()).toBe(2);
    });

    it('clamps out-of-range selectedIndex (does not write activeStep)', () => {
      component.activeStep.set(1);
      // Negative or >3 should be ignored — Material stepper already
      // clamps, but the handler defends defensively.
      component.onStepperSelectionChange({ selectedIndex: -1 });
      expect(component.activeStep()).toBe(1);
      component.onStepperSelectionChange({ selectedIndex: 4 });
      expect(component.activeStep()).toBe(1);
    });

    it('is idempotent when selectedIndex === activeStep()', () => {
      component.activeStep.set(2);
      // No-op signal.set call — just verifying no spurious write
      // (signal-equality dedupes on its own; this asserts the
      // short-circuit guard is explicit).
      component.onStepperSelectionChange({ selectedIndex: 2 });
      expect(component.activeStep()).toBe(2);
    });
  });

  describe('W1 — auto-advance to Step 4 on successful execute', () => {
    // The terminal-poll handler flips `activeStep` to 3 (Result)
    // on a `succeeded` run. `failed` and `interrupted` runs stay
    // on Step 3 (the operator is already looking at the destructive
    // affordances; the failure / interrupted state renders inline
    // there). The mirror's `startPolling` is private, so we exercise
    // the path via `onExecute()` → confirm-true → poll-terminal.
    it('flips activeStep to 3 after a succeeded run', () => {
      component.activeStep.set(2);
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      component.onExecute();
      // pollRun emits a single succeeded row synchronously; the
      // terminal branch (mirror of production) calls set(3) BEFORE
      // refreshStatus + unsubscribe (local-ref `sub` would abort
      // the rest of the callback if unsubscribe ran first).
      expect(component.activeStep()).toBe(3);
    });

    it('failed execute does NOT advance — activeStep stays 2 (Step 3)', () => {
      // Override pollRun to emit a terminal `failed` row synchronously,
      // mirroring the re-entry resume test's mock-overrides. Mirrors
      // production's terminal branch: status === 'succeeded' is the
      // ONLY trigger for activeStep.set(3); failed/interrupted stay
      // put and render their failure / interrupted UI inline on
      // Step 3.
      component.activeStep.set(2);
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      service.pollRun = (
        runId: string,
        intervalMs?: number,
        hintMs?: number | null,
      ) => {
        service.pollRunCalls.push({ runId, intervalMs, hintMs });
        return of(RUN_FAILED_NEVER_PRUNED);
      };
      component.onExecute();
      expect(component.activeStep()).toBe(2);
    });

    it('interrupted execute does NOT advance — activeStep stays 2 (Step 3)', () => {
      // Same mock-override strategy as the failed case above; status
      // === 'interrupted' is the AM-6 re-run affordance signal —
      // the operator stays on Step 3 to see the inline interrupted UI
      // and click "Run again to converge".
      component.activeStep.set(2);
      service.lastDryRun.set(DRY_RUN);
      mockDialog.nextResult = true;
      service.pollRun = (
        runId: string,
        intervalMs?: number,
        hintMs?: number | null,
      ) => {
        service.pollRunCalls.push({ runId, intervalMs, hintMs });
        return of(RUN_INTERRUPTED_NEVER_PRUNED);
      };
      component.onExecute();
      expect(component.activeStep()).toBe(2);
    });
  });

  describe('step gating — canContinueFromStep1 (AC-4)', () => {
    it('is true when canDryRun && !maintenanceDisabled && !isRunInFlight', () => {
      service.canDryRun.set(true);
      service.isRunInFlight.set(false);
      service.lastError.set(null);
      expect(component.canContinueFromStep1()).toBe(true);
    });

    it('is false when canDryRun() is false', () => {
      service.canDryRun.set(false);
      expect(component.canContinueFromStep1()).toBe(false);
    });

    it('is false when maintenance is disabled (kill-switch)', () => {
      service.canDryRun.set(true);
      service.lastError.set({ error: 'maintenance_disabled', message: 'kill' });
      expect(component.isMaintenanceDisabled()).toBe(true);
      expect(component.canContinueFromStep1()).toBe(false);
    });

    it('is false when a daemon run is in flight (AC-16)', () => {
      service.canDryRun.set(true);
      service.isRunInFlight.set(true);
      expect(component.canContinueFromStep1()).toBe(false);
    });
  });

  describe('step gating — canContinueFromStep2 (spec §2.4)', () => {
    it('is true when lastDryRun exists with would_delete_count > 0 AND not in flight', () => {
      service.lastDryRun.set(DRY_RUN);
      service.canDryRun.set(true);
      service.isRunInFlight.set(false);
      expect(component.canContinueFromStep2()).toBe(true);
    });

    it('is false when no dry-run is on record', () => {
      service.lastDryRun.set(null);
      expect(component.canContinueFromStep2()).toBe(false);
    });

    it('is false when dry-run reports would_delete_count == 0', () => {
      const zero: CheckpointCleanupDryRun = {
        ...DRY_RUN,
        would_delete_count: 0,
      };
      service.lastDryRun.set(zero);
      expect(component.canContinueFromStep2()).toBe(false);
    });

    it('is false during dry-run / execute / isRunInFlight (AC-16)', () => {
      service.lastDryRun.set(DRY_RUN);
      component.dryRunning.set(true);
      expect(component.canContinueFromStep2()).toBe(false);
      component.dryRunning.set(false);
      component.executing.set(true);
      expect(component.canContinueFromStep2()).toBe(false);
      component.executing.set(false);
      service.isRunInFlight.set(true);
      expect(component.canContinueFromStep2()).toBe(false);
    });
  });

  describe('step gating — canContinueFromStep3 (spec §2.5)', () => {
    it('is true when dry-run exists AND not executing AND not in-flight', () => {
      service.lastDryRun.set(DRY_RUN);
      expect(component.canContinueFromStep3()).toBe(true);
    });

    it('is false when no dry-run on record', () => {
      service.lastDryRun.set(null);
      expect(component.canContinueFromStep3()).toBe(false);
    });

    it('is false during executing (AC-16)', () => {
      service.lastDryRun.set(DRY_RUN);
      component.executing.set(true);
      expect(component.canContinueFromStep3()).toBe(false);
    });

    it('is false when daemon-reported in-flight (AC-16)', () => {
      service.lastDryRun.set(DRY_RUN);
      service.isRunInFlight.set(true);
      expect(component.canContinueFromStep3()).toBe(false);
    });
  });

  describe('in-flight handling (AC-16)', () => {
    it('dry-run and execute gates both lock when isRunInFlight is true', () => {
      service.lastDryRun.set(DRY_RUN);
      service.isRunInFlight.set(true);
      expect(component.canContinueFromStep1()).toBe(false);
      expect(component.canContinueFromStep3()).toBe(false);
    });

    it('dry-run gate stays locked when local dryRunning() is true', () => {
      service.lastDryRun.set(DRY_RUN);
      component.dryRunning.set(true);
      expect(component.canContinueFromStep2()).toBe(false);
    });
  });

  describe('Status Strip render (spec §2.1)', () => {
    it('keepN renders the configured max_per_thread', () => {
      service.status.set({
        ...STATUS,
        config: {
          checkpoint_max_per_thread: 6,
          cleanup_interval_hours: 6,
          blob_prune_dry_run_env_default: '1',
          blob_prune_destructive_armed: false,
        },
      });
      expect(component.statusStripKeepN()).toBe('6');
    });

    it('keepN renders "—" when status is missing', () => {
      service.status.set(null);
      expect(component.statusStripKeepN()).toBe('—');
    });

    it('keepN renders "—" when maintenance is disabled', () => {
      service.lastError.set({ error: 'maintenance_disabled', message: 'off' });
      service.status.set({
        ...STATUS,
        config: {
          checkpoint_max_per_thread: 6,
          cleanup_interval_hours: 6,
          blob_prune_dry_run_env_default: '1',
          blob_prune_destructive_armed: false,
        },
      });
      expect(component.statusStripKeepN()).toBe('—');
    });

    it('lastRun renders "Never run" when status().last_run is null', () => {
      service.status.set({ ...STATUS, last_run: null });
      expect(component.statusStripLastRun()).toBe('Never run');
    });

    it('lastRun renders "{status} · {bytes} · {time}" when a row exists', () => {
      service.status.set({
        ...STATUS,
        last_run: {
          run_id: 'ckpt-1',
          kind: 'manual_execute',
          started_at: '2026-10-01T02:14:00.000Z',
          completed_at: '2026-10-01T02:21:00.000Z',
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
            duration_ms: 192000,
          },
        },
      });
      const out = component.statusStripLastRun();
      expect(out).toContain('succeeded');
      expect(out).toContain('256 MB');
    });

    it('dryRunFresh renders "Stale — run dry-check" when no dry-run', () => {
      service.lastDryRun.set(null);
      expect(component.statusStripDryRunFresh()).toBe('Stale — run dry-check');
    });

    it('dryRunFresh renders "{scanned} pairs · expires in {time}" when present', () => {
      service.lastDryRun.set(DRY_RUN);
      const out = component.statusStripDryRunFresh();
      expect(out).toMatch(/^\d+ pairs · expires in /);
    });
  });
});

// ── Orientation locator contract (spec amend v3 validation clause) ────────
//
// Spec amend v3 pins a Playwright e2e locator:
//   `mat-stepper[orientation="horizontal"]`
// against the rendered DOM when isDesktop() is true (and the vertical
// variant when isDesktop() is false). The production template now places
// the four `<mat-step>` blocks as DIRECT children of each
// `<mat-stepper>` (CRITICAL-3 Option A restructure — the previous
// `<ng-container *ngTemplateOutlet>` indirection broke the
// ContentChildren contract and rendered an empty stepper, empirically
// verified). Static `orientation="horizontal"` / `orientation="vertical"`
// attributes land on the rendered host element regardless of structural
// context; these tests pin that contract at the rendered-DOM level.
//
// The rest of this file uses a logic-mirror pattern with no TestBed (a
// deliberate design choice — see file header). These two tests use a
// minimal TestBed harness in an isolated describe block so the contract
// is verified at the rendered-DOM level without perturbing the other
// tests. NO_ERRORS_SCHEMA skips Angular Material element validation
// (`<mat-stepper>`, `<mat-step>`, etc.) — the static `orientation="..."`
// attribute lands on the rendered host element regardless.
//
// BREAKPOINT STUB NOTE — the previous `NG0201: No provider found for
// _CdkStepper` failure was caused by the `*ngTemplateOutlet`
// indirection: outlet-stamped `<mat-step>` elements never registered
// with the parent `<mat-stepper>`'s ContentChildren query, so the
// stepper rendered empty (and any harness that surfaced the DI chain
// surfaced NG0201 instead). The restructure (direct mat-step children)
// lets the production template render under TestBed without an
// override — this describe no longer defines an `orientationPinTemplate`.
//
// If either of the locator tests fails (attribute absent from the
// rendered DOM), the production fix is to add `[attr.orientation]="..."`
// alongside the existing inputs on both `<mat-stepper>` tags in the
// template — attr bindings guarantee the literal DOM attribute.
//
// Parity guard (added in this restructure) — the set of
// `[data-testid]` attributes rendered in desktop mode MUST equal the
// set rendered in narrow mode; this catches any drift between the two
// duplicated `<mat-step>` block copies. If a future edit diverges one
// branch, the parity assertion fails immediately.
describe('CheckpointCleanupComponent — orientation locator contract (spec amend v3 validation clause)', () => {
  let fixture: ComponentFixture<CheckpointCleanupComponent>;

  async function renderWithMatches(matches: boolean): Promise<void> {
    // Stub BreakpointObserver — synchronous emit of the requested
    // `matches` value, identical shape to the production ObserveResult
    // (BreakpointObserver emits { matches, breakpoints }).
    const stubBPO = {
      observe: jest.fn().mockReturnValue(
        of({ matches, breakpoints: { '(min-width: 1024px)': matches } }),
      ),
    };

    // Fresh service mock per test so signal state doesn't leak across
    // tests (the existing `MockCheckpointCleanupService` mirrors
    // production surface — full method coverage is not required here,
    // only fetchStatus() because ngOnInit calls refreshStatus()).
    const serviceStub = new MockCheckpointCleanupService();
    // STATUS fixture has no last_run and no in_flight — ngOnInit's
    // refreshStatus() subscription handlers will no-op cleanly.
    serviceStub.status.set(STATUS);

    // Compile the REAL component (no template override) with the
    // BreakpointObserver stub. MatDialog / MatSnackBar / service are
    // provided as test doubles — same shape as the existing tests.
    await TestBed.configureTestingModule({
      imports: [CheckpointCleanupComponent, MatStepperModule],
      providers: [
        { provide: BreakpointObserver, useValue: stubBPO },
        { provide: CheckpointCleanupService, useValue: serviceStub },
        { provide: MatDialog, useValue: mockDialog },
        { provide: MatSnackBar, useValue: new MockSnackBar() },
      ],
      schemas: [CUSTOM_ELEMENTS_SCHEMA, NO_ERRORS_SCHEMA],
    }).compileComponents();

    fixture = TestBed.createComponent(CheckpointCleanupComponent);
    // createComponent() runs the component's field initializers BEFORE
    // returning — including the BreakpointObserver.subscribe(...)
    // that pipes the stub's synchronous emit through to isDesktop,
    // so isDesktop is already correct by the time we reach the
    // detectChanges() call below. The single detectChanges() at the
    // bottom of this function both triggers ngOnInit → refreshStatus()
    // → service.fetchStatus subscription AND flushes the @if/@else
    // orientation branch (which then renders the correct mat-stepper
    // instance against the active value isDesktop set above).
    fixture.detectChanges();
  }

  afterEach(() => {
    fixture?.destroy();
    TestBed.resetTestingModule();
    mockDialog.reset();
    MockSnackBarRef.reset();
  });

  /**
   * Extract the set of `[data-testid]` attribute values rendered in the
   * current fixture DOM. Used by the parity guard — both orientation
   * branches MUST render the same set of testids.
   */
  function renderedTestIds(): Set<string> {
    const nodes = fixture.nativeElement.querySelectorAll('[data-testid]');
    const ids = new Set<string>();
    nodes.forEach((el: Element) => {
      const v = el.getAttribute('data-testid');
      if (v) ids.add(v);
    });
    return ids;
  }

  it('renders the 4 mat-step blocks and the horizontal mat-stepper when isDesktop=true', async () => {
    await renderWithMatches(true);

    // (a) exactly 4 mat-step-header elements render
    const headers = fixture.nativeElement.querySelectorAll('mat-step-header');
    expect(headers.length).toBe(4);

    // (b) mat-stepper[orientation="horizontal"] matches 1 / vertical 0 (desktop)
    const horizontal = fixture.nativeElement.querySelectorAll(
      'mat-stepper[orientation="horizontal"]',
    );
    const vertical = fixture.nativeElement.querySelectorAll(
      'mat-stepper[orientation="vertical"]',
    );
    expect(horizontal.length).toBe(1);
    expect(vertical.length).toBe(0);
  });

  it('renders the 4 mat-step blocks and the vertical mat-stepper when isDesktop=false', async () => {
    await renderWithMatches(false);

    // (a) exactly 4 mat-step-header elements render
    const headers = fixture.nativeElement.querySelectorAll('mat-step-header');
    expect(headers.length).toBe(4);

    // (b) inverse of desktop: mat-stepper[orientation="vertical"] matches 1 / horizontal 0
    const horizontal = fixture.nativeElement.querySelectorAll(
      'mat-stepper[orientation="horizontal"]',
    );
    const vertical = fixture.nativeElement.querySelectorAll(
      'mat-stepper[orientation="vertical"]',
    );
    expect(horizontal.length).toBe(0);
    expect(vertical.length).toBe(1);
  });

  // (c) PARITY: the set of [data-testid] attributes rendered in
  // desktop mode MUST equal the set rendered in narrow mode. This
  // guards the duplicated <mat-step> blocks against drift forever —
  // a future edit that diverges one branch fails this assertion
  // immediately, surfacing the divergence before it can ship.
  it('renders the same set of [data-testid] attributes in desktop and narrow modes (parity guard)', async () => {
    await renderWithMatches(true);
    const desktopTestIds = renderedTestIds();

    // Reset TestBed between renders — `renderWithMatches` calls
    // `TestBed.configureTestingModule` which throws if the test
    // module is already instantiated. The describe-level `afterEach`
    // runs once per test, not per render, so we reset explicitly
    // between the two renders.
    fixture.destroy();
    TestBed.resetTestingModule();
    mockDialog.reset();
    MockSnackBarRef.reset();

    await renderWithMatches(false);
    const narrowTestIds = renderedTestIds();

    expect(narrowTestIds.size).toBeGreaterThan(0);
    expect(desktopTestIds).toEqual(narrowTestIds);
  });
});
