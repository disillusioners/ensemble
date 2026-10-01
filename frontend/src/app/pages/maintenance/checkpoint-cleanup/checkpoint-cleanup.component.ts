import {
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatStepperModule, MatStepper } from '@angular/material/stepper';
import { BreakpointObserver } from '@angular/cdk/layout';
import { map } from 'rxjs/operators';
import { Subscription } from 'rxjs';
import { CheckpointCleanupService } from './checkpoint-cleanup.service';
import { ConfirmDialogComponent } from '../../../components/confirm-dialog/confirm-dialog.component';
import type {
  CheckpointCleanupBlobsSummary,
  CheckpointCleanupDryRun,
  CheckpointCleanupExecute,
  CheckpointCleanupExecuteRequest,
  CheckpointCleanupRun,
  CheckpointCleanupStatus,
  MaintenanceDisplayCode,
  MaintenanceErrorBody,
} from '../../../models';

/**
 * Checkpoint Cleanup section — full UI.
 *
 * ck-redesign-2026q4 — 4-step Material wizard + persistent Status Strip.
 * DOM order: kill-switch / origin-guard banners → Status Strip → mat-stepper
 * → custom footer (Back / Back to start / Continue / Cleanup now per step) →
 * page-level Debug expander (raw JSON). Stepper orientation is
 * viewport-conditional via `@if (isDesktop())` (BreakpointObserver:
 * ≥1024px → horizontal, below → vertical) — TWO source-level `<mat-stepper>`
 * instances (one per orientation branch), each rendering its four
 * `<mat-step>` children from the shared `ckStepContent` `<ng-template>`
 * (no duplication of step bodies). `[linear]="false" [selectedIndex]="activeStep()"`
 * on both branches. Step content blocks preserve every existing
 * `data-testid`; the per-card Raw JSON `<details>` blocks were
 * consolidated into the page-level Debug expander (AC-13).
 *
 * Step 4 auto-renders post-execute via `lastExecuteResult()`; with
 * `[linear]="false"` a page refresh during execute lands the operator
 * on Step 4 with the actual result (the BE's `status.last_run` is
 * the persistence source — see `seedBannerFromStatus()`). Status
 * Strip decouples monitoring from the wizard (AC-1, AC-11).
 *
 * AC-4 / AC-16 — Continue is gated on `canContinueFromStep1()` /
 * `canContinueFromStep2()` / `canContinueFromStep3()`; in-flight
 * disables dry-run + execute on all steps, with an inline notice
 * rendered ONLY in Step 1 (the spec's narrow landing).
 *
 * AM-14 — 409-adoption: on 409 from `service.execute()`, adopt
 * `details.run_id` and resume polling (no error toast).
 * AM-11 — dual-flavor branch on `summary.blobs.destructive:bool`.
 * AM-10 — `skipped[]` render with summary line + reason badge map
 * (`ZERO_REFS_FAIL_SAFE` / `MAX_REFS_EXCEEDED` / `ERROR:*`) +
 * `skipped_truncated` notice.
 * AM-6 — interrupted-state affordance ("Daemon restarted mid-run —
 * re-run to converge").
 * AM-12 — `expected_duration_ms_hint` display in the executing card.
 * AM-13 — `maintenance_disabled` global banner above all cards.
 * AM-17 — execute payload is EXACTLY `{dry_run_run_id, expected_bytes,
 * confirm: true}` — NO `idempotency_key`, NO `crypto.randomUUID`.
 * AM-16 — honest duration copy ("May take several minutes on large
 * databases") + `fresh_until` rendered in the dry-run result panel.
 *
 * v3.2 — three additive projection fields on the dry-run response
 * (`bytes_reclaimable_now`, `bytes_reclaimable_after_row_prune`,
 * `bytes_reclaimable_total`) PLUS a two-field `projection` block on
 * the manual_execute run summary (`bytes_reclaimable_now_at_dry_run`,
 * `bytes_reclaimable_after_row_prune_at_dry_run`). Echo gate AM-3
 * UNCHANGED — these fields are informational and projection-class;
 * the existing `would_free_bytes` echo pin is untouched. Rendered as:
 *
 *   - Dry-run card three-number render + skip-flag honesty banner
 *     (R-1, R-4)
 *   - Confirm dialog journey copy (now + "after" + "running cleanup
 *     again" anchor)
 *   - Post-run convergence banner ("Run cleanup again to reclaim ~X
 *     more" — a NEW dry-run, never a silent execute); hides when a
 *     fresh dry-run reports `now == 0` (convergence reached)
 */
@Component({
  selector: 'app-checkpoint-cleanup',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatDialogModule,
    MatStepperModule,
  ],
  templateUrl: './checkpoint-cleanup.component.html',
  styleUrl: './checkpoint-cleanup.component.scss',
})
export class CheckpointCleanupComponent implements OnInit, OnDestroy {
  private readonly service = inject(CheckpointCleanupService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);
  private readonly breakpoints = inject(BreakpointObserver);

  /** Stepper orientation breakpoint (spec §2.2 amendment, AC-2 / AC-15).
   *  `isDesktop()` is `true` when the viewport is ≥1024px wide (the
   *  horizontal-orientation arm). BreakpointObserver emits synchronously
   *  on subscribe so the signal seeds correctly on initial render —
   *  no orientation flash on first paint. */
  readonly isDesktop = signal(false);
  private readonly bpSub: Subscription = this.breakpoints
    .observe(['(min-width: 1024px)'])
    .pipe(map((r) => r.matches))
    .subscribe((matches) => this.isDesktop.set(matches));

  // ── Service signals re-exposed for the template ───────────────────────
  readonly status = this.service.status;
  readonly lastDryRun = this.service.lastDryRun;
  readonly lastError = this.service.lastError;
  readonly canDryRun = this.service.canDryRun;
  readonly isRunInFlight = this.service.isRunInFlight;
  // Item 12 — the `isReady` re-exposed alias was dead surface
  // (template never reads it; the gear-menu probe in app.ts
  // branches directly on the `state` enum of the Availability
  // body). The service's `isReady` computed is also removed (see
  // service.ts); component-side alias goes with it.

  // ── Local UI state ────────────────────────────────────────────────────
  readonly dryRunning = signal(false);
  readonly executing = signal(false);
  /** AM-12 — display hint from the 202 body. null until first 202 received. */
  readonly expectedDurationHintMs = signal<number | null>(null);
  /** Last completed polled run (set on `pollRun` terminal emission). */
  readonly lastExecuteResult = signal<CheckpointCleanupRun | null>(null);
  readonly activeRunId = signal<string | null>(null);
  /**
   * v3.2 B1 — `run_id` of the dry-run that backed the most recent
   * execute (set in `performExecute`). The post-run banner's
   * "hide on convergence" check (`lastDryRun.bytes_reclaimable_now
   * === 0`) is SCOPED by this signal: the rule fires ONLY on a
   * FRESH dry-run whose `run_id` differs from this anchor — i.e.
   * a dry-run that the user ran AFTER the execute, not the dry-run
   * that the execute was confirmed against. Without this scope,
   * on a never-pruned DB the pre-execute dry-run itself reports
   * `now == 0` (the incident scenario) and the banner never
   * appears.
   */
  readonly executedDryRunId = signal<string | null>(null);
  /** Whether the dry-run is stale (drives the warning border on the dry-run button). */
  readonly dryRunIsStale = computed(() =>
    this.service.isDryRunStale(this.lastDryRun()),
  );

  // ── Wizard state (spec ck-redesign-2026q4 §2.2 / AC-9) ─────────────────
  /** Index of the currently visible step (0..3). Backs `<mat-stepper [selectedIndex]>`. */
  readonly activeStep = signal(0);

  /**
   * Stepper navigation facade — the template binds to these
   * helpers via `stepperNav().next()` / `.back()` / `.backToStart()`.
   * Centralizes the index math so a future insertion of a new step
   * requires only the boundary checks here, not template-side edits.
   */
  readonly stepperNav = () => ({
    next: () => this.activeStep.update((i) => Math.min(i + 1, 3)),
    back: () => this.activeStep.update((i) => Math.max(i - 1, 0)),
    backToStart: () => this.activeStep.set(0),
  });

  /**
   * W1 — sync `activeStep()` with Material's `<mat-stepper>` selection.
   * Bound to `(selectionChange)` on BOTH orientation branches in the
   * template. Without this, header-driven step jumps leave the custom
   * footer rendering the WRONG active-step buttons (per-step branch
   * in the template reads `activeStep()`, but the stepper's internal
   * `selectedIndex` would diverge). The handler clamps to the valid
   * 0..3 range as a defensive belt — the stepper already clamps.
   */
  onStepperSelectionChange(stepper: MatStepper | undefined): void {
    if (!stepper) {
      return;
    }
    const idx = stepper.selectedIndex;
    if (idx < 0 || idx > 3) {
      return;
    }
    if (idx !== this.activeStep()) {
      this.activeStep.set(idx);
    }
  }

  // ── Status Strip render helpers (spec §2.1) ────────────────────────────
  /** Keep N — first tile of the Status Strip. "—" when disabled/killed. */
  statusStripKeepN(): string {
    if (this.isMaintenanceDisabled() || !this.status()) {
      return '—';
    }
    const v = this.status()?.config?.checkpoint_max_per_thread;
    return typeof v === 'number' ? String(v) : '—';
  }

  /**
   * Last-run summary line — second tile. Format: "{status} · {freed} · {completed_at}"
   * (destructive uses bytes_freed; dry-flavor would_free_bytes). "Never run"
   * when status is missing or no prior row.
   */
  statusStripLastRun(): string {
    if (this.isMaintenanceDisabled() || !this.status()) {
      return '—';
    }
    const last = this.status()?.last_run;
    if (!last) {
      return 'Never run';
    }
    const bytes =
      last.summary?.blobs?.bytes_freed ?? last.summary?.blobs?.would_free_bytes ?? 0;
    const statusLabel = last.status ?? 'unknown';
    const time = this.formatTimestamp(last.completed_at);
    return `${statusLabel} · ${this.formatBytes(bytes)} · ${time}`;
  }

  /**
   * Dry-run fresh — third tile. Format: "{scanned} pairs · expires in {fresh_until}".
   * "Stale — run dry-check" when no dry-run on record.
   */
  statusStripDryRunFresh(): string {
    if (this.isMaintenanceDisabled() || !this.lastDryRun()) {
      return 'Stale — run dry-check';
    }
    const dry = this.lastDryRun()!;
    const scanned = dry.scanned?.thread_ns_pairs ?? 0;
    const time = this.formatTimestamp(dry.fresh_until);
    return `${scanned} pairs · expires in ${time}`;
  }

  // ── Step gating (spec §2.3–§2.5) ───────────────────────────────────────
  /** Step 1 → Step 2. Disabled on kill-switch / in-flight / no-dry-run gate. */
  canContinueFromStep1(): boolean {
    return this.canDryRun() && !this.isMaintenanceDisabled() && !this.isRunInFlight();
  }

  /**
   * Step 2 → Step 3. Gated on a fresh dry-run that promises something
   * to delete. The spec's "OR explicit operator override" arm is
   * satisfied by `dry.would_delete_count > 0` OR a successful prior
   * execute (the run-again banner path); for the wizard gating path
   * we use the strict arm — the banner already offers the override.
   */
  canContinueFromStep2(): boolean {
    const dry = this.lastDryRun();
    if (!dry) {
      return false;
    }
    if (this.dryRunning() || this.executing() || this.isRunInFlight()) {
      return false;
    }
    return (dry.would_delete_count ?? 0) > 0;
  }

  /** Step 3 action — gated on dry-run AND not already executing / in-flight. */
  canContinueFromStep3(): boolean {
    return !!this.lastDryRun() && !this.executing() && !this.isRunInFlight();
  }

  // Polling subscription — track so OnDestroy can tear down
  private pollSub: Subscription | null = null;

  ngOnInit(): void {
    this.refreshStatus();
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
    this.bpSub.unsubscribe();
  }

  // ── Actions ───────────────────────────────────────────────────────────

  refreshStatus(): void {
    this.service.fetchStatus().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (status: CheckpointCleanupStatus) => {
        // v3.2 B2 — seed the post-run banner from `status.last_run`
        // when the page loads (or refreshes) mid-/post-execute. The
        // banner is a UI affordance, not a poll outcome; without this
        // seed it only appears for runs the user personally watched
        // to terminal. The wire-level run-row projection echo
        // (R-5) IS the persistence source — auto rows have no
        // projection block and auto rows therefore never seed the
        // banner.
        this.seedBannerFromStatus(status);
        // Fix commission 2026-09-29 — re-entry: if the BE reports
        // an in-flight run (page refreshed mid-poll, or another
        // tab started a run), resume polling that run automatically.
        // Without this branch a refresh mid-run orphans the FE
        // poll and the terminal state never surfaces.
        this.resumePollingIfInFlight(status);
      },
      error: () => {
        // Already surfaced via service.lastError.
      },
    });
  }

  /**
   * Fix commission 2026-09-29 — re-entry resume. On
   * `refreshStatus()` landing, if `status.in_flight` carries a
   * `run_id`, start polling it (without a hint — re-entry has no
   * hint; the service defaults to the floor budget and the
   * post-cap backoff carries the load). Idempotent — `activeRunId()`
   * is the source of truth: if a poll is already active against
   * the same id, the early return short-circuits; a different id
   * takes over (the prior subscription is unsubscribed by
   * `startPolling`).
   *
   * `in_flight` is non-null ONLY while the run is non-terminal
   * (BE clears the slot on terminal). The `activeRunId()` guard
   * covers the rapid double-fetch edge case where two
   * `refreshStatus()` calls fire in quick succession.
   *
   * Re-entry feedback-loop guard (iter2 review finding 2): the
   * terminal handler nulls `activeRunId` BEFORE calling
   * `refreshStatus()`, so the `activeRunId()` guard alone is not
   * sufficient — if the BE transiently retains `in_flight` after
   * the run reached terminal, a post-terminal refreshStatus()
   * would see `in_flight` set, find `activeRunId` null, start a
   * new poll subscription, observe the terminal value, fire the
   * terminal handler again, which calls `refreshStatus()`
   * again → tight loop until the BE finally clears the slot.
   * The `lastExecuteResult()` guard breaks that loop by treating
   * "we already know this run_id is terminal" as the source of
   * truth: the terminal handler has already fired for this id
   * and nothing more can be learned from another poll round.
   */
  private resumePollingIfInFlight(status: CheckpointCleanupStatus): void {
    const inFlight = status.in_flight;
    if (!inFlight || !inFlight.run_id) {
      return;
    }
    if (this.activeRunId() === inFlight.run_id) {
      // Already polling this id — nothing to do. The
      // `activeRunId` match is the source of truth (the
      // terminal-stop handler nulls `activeRunId` before any
      // post-terminal refresh fires).
      return;
    }
    // Feedback-loop guard (iter2 review finding 2): if we already
    // observed a terminal row for this id via a prior poll round,
    // the terminal handler has fired and any further poll only
    // re-emits the same terminal value (which the BE has now
    // caught up with). Suppressing here breaks the
    // refreshStatus → resume → poll-terminal → terminal-handler
    // → refreshStatus loop that a BE-side `in_flight`-retained
    // window would otherwise trigger.
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
   * with `bytes_reclaimable_after_row_prune_at_dry_run > 0`. Auto
   * rows are skipped (no projection block, R-5). Failed/interrupted
   * runs are skipped (banner is success-only). Page refresh
   * mid-poll re-runs `refreshStatus()` on init and the banner
   * re-appears without any client action.
   *
   * v3.2 B1 follow-up — `executedDryRunId` is performExecute-owned
   * state (set when the execute is dispatched). The seed MUST NOT
   * touch the anchor: on the live execute path, the poll-terminal
   * handler calls `refreshStatus()` immediately after
   * `lastExecuteResult` is set, so wiping the anchor here would
   * re-enable the hide rule to fire on the pre-execute dry-run
   * itself (now==0 on never-pruned profiles) and the banner would
   * never appear — the original B1 incident. On a fresh page-load
   * session the anchor is already null (no performExecute this
   * session); the seed leaves it null.
   */
  private seedBannerFromStatus(status: CheckpointCleanupStatus): void {
    const last = status.last_run;
    if (!last) {
      return;
    }
    // R-5 — only `manual_execute` rows carry the additive
    // projection block; auto rows are skipped.
    if (last.kind !== 'manual_execute') {
      return;
    }
    if (last.status !== 'succeeded') {
      return;
    }
    if (!last.summary) {
      return;
    }
    const after =
      last.summary.projection?.bytes_reclaimable_after_row_prune_at_dry_run ?? 0;
    if (after <= 0) {
      return;
    }
    // Reconstruct a `CheckpointCleanupRun` from the `last_run`
    // shape (the wire row carries no `error` field — banner source
    // runs are succeeded by definition).
    this.lastExecuteResult.set({
      run_id: last.run_id,
      kind: last.kind,
      status: last.status,
      started_at: last.started_at,
      completed_at: last.completed_at,
      summary: last.summary,
      error: null,
    });
    // The anchor (`executedDryRunId`) is intentionally untouched.
    // See the class JSDoc above for the rationale — wiping it here
    // would break the live-execute path.
  }

  onDryRun(): void {
    if (!this.canDryRun() || this.dryRunning()) {
      return;
    }
    this.dryRunning.set(true);
    // Item 14 — optimistic lastError clear at the top of onDryRun().
    // The dry-run request MAY clear an existing banner before it
    // even fires; if the request then fails, the new error replaces
    // the cleared one. If the request succeeds, the banner stays
    // gone (no stale error from a prior run). Net effect: the user
    // sees the current operation's outcome, not the last.
    this.service.clearLastError();
    this.service.dryRun().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.dryRunning.set(false);
      },
      error: () => {
        this.dryRunning.set(false);
        // Surfaced via service.lastError; the inline banner renders it.
        this.showSnackForLastError();
      },
    });
  }

  onExecute(): void {
    const dryRun = this.lastDryRun();
    // Pre-flight: must have a dry-run AND it must be fresh.
    if (!dryRun) {
      this.snackBar.open(
        'Run the dry-run check first.',
        'Dismiss',
        { duration: 4000, panelClass: 'error-snackbar' },
      );
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
      // AM-16 — `false | undefined` is cancel; only `true` proceeds.
      if (!confirmed) {
        return;
      }
      // Item 6 — re-check `isDryRunStale` BEFORE `performExecute`.
      // The confirm dialog may have been open long enough for the
      // dry-run's `fresh_until` to expire (server-side backstop is
      // the authoritative gate; this is the FE-side echo). The check
      // uses the SAME `dryRun` reference the user confirmed against;
      // a stale read here aborts cleanly with a re-run snack-bar
      // and never hits the execute endpoint.
      if (this.service.isDryRunStale(dryRun)) {
        this.lastDryRun.set(null); // force re-run
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
    if (this.executing()) {
      return; // belt + braces against double-click
    }
    this.executing.set(true);
    this.lastExecuteResult.set(null);
    this.expectedDurationHintMs.set(null);
    // Item 14 — optimistic lastError clear at the top of
    // performExecute(). Same rationale as onDryRun(): the current
    // operation's outcome is what the user sees; stale errors from
    // a prior run don't bleed into the new attempt.
    this.service.clearLastError();
    // v3.2 B1 — anchor the banner's "hide on convergence" rule.
    // The dry-run whose `run_id` is captured here is the one the
    // execute was confirmed against; the post-run banner's hide
    // check excludes it so on never-pruned DBs (where the
    // pre-execute dry-run already reports `now == 0`) the banner
    // still renders. A subsequent FRESH dry-run with a different
    // `run_id` and `now == 0` then hides the banner as before.
    this.executedDryRunId.set(dryRun.run_id);

    // AM-17 — payload is EXACTLY `{dry_run_run_id, expected_bytes,
    // confirm: true}` — NO idempotency key, NO client-side UUID generation.
    const payload: CheckpointCleanupExecuteRequest = {
      dry_run_run_id: dryRun.run_id,
      expected_bytes: dryRun.would_free_bytes,
      confirm: true,
    };

    this.service.execute(payload).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (body: CheckpointCleanupExecute) => {
        this.activeRunId.set(body.run_id);
        // Fix commission 2026-09-29 — pass the 202 body's
        // `expected_duration_ms_hint` through to `startPolling` so
        // the service can size the active poll budget
        // (`max(hint × N, floor)`) per the new contract.
        this.expectedDurationHintMs.set(body.expected_duration_ms_hint);
        this.startPolling(body.run_id, body.expected_duration_ms_hint);
      },
      error: (err: MaintenanceErrorBody) => {
        // AM-14, AM-17 — 409-adoption. If `run_in_flight` carries a
        // `details.run_id`, adopt it and resume polling — NO error
        // toast AND no inline error banner. The 409 response IS the
        // de-facto idempotency handle; we clear `lastError` so the
        // banner doesn't render during the "ride along" polling.
        const adoptedRunId = this.service.adoptRunIdFromError(err);
        if (adoptedRunId) {
          this.service.clearLastError();
          this.activeRunId.set(adoptedRunId);
          // No snack-bar for this case — ride along UX.
          // No hint is available on the 409 body; the service
          // defaults to the floor budget.
          this.startPolling(adoptedRunId);
          return;
        }
        // Fallthrough: malformed body / different error — surface.
        this.executing.set(false);
        this.showSnackForLastError();
      },
    });
  }

  private startPolling(runId: string, hintMs: number | null = null): void {
    // AM-6 — poll terminates on `succeeded | failed | interrupted`.
    // Fix commission 2026-09-29 — `hintMs` plumbs through to the
    // service so the active budget is hint-sized; the post-cap
    // backoff phase keeps polling until terminal or teardown.
    this.pollSub?.unsubscribe();
    this.pollSub = this.service.pollRun(runId, undefined, hintMs).subscribe({
      next: (run: CheckpointCleanupRun) => {
        this.lastExecuteResult.set(run);
        if (
          run.status === 'succeeded' ||
          run.status === 'failed' ||
          run.status === 'interrupted'
        ) {
          this.executing.set(false);
          this.activeRunId.set(null);
          this.pollSub?.unsubscribe();
          this.pollSub = null;
          // Re-fetch status so `last_run` reflects the new row.
          this.refreshStatus();
          // W1 — auto-advance to Step 4 (Result) on successful execute.
          // The result panel is rendered from `lastExecuteResult()`, which
          // is set above; this `activeStep.set(3)` flips the stepper's
          // selectedIndex so the operator lands on the Result step
          // without a manual navigation. `succeeded` only — `failed`
          // surfaces inline on Step 3, `interrupted` surfaces inline on
          // Step 3 (the rerun card); both stay on the Confirm step.
          // The header `(selectionChange)` handler is the read-side
          // back-pressure: any stepper-side jump propagates back into
          // `activeStep()` so the footer always renders the active
          // step's buttons.
          if (run.status === 'succeeded') {
            this.activeStep.set(3);
            this.snackBar.open('Cleanup succeeded.', 'Dismiss', {
              duration: 4000,
              panelClass: 'success-snackbar',
            });
          } else if (run.status === 'failed') {
            this.snackBar.open(
              `Cleanup failed: ${run.error?.message ?? 'unknown error'}`,
              'Dismiss',
              { duration: 6000, panelClass: 'error-snackbar' },
            );
          }
          // AM-6 — `interrupted` has no toast (the result panel renders
          // the inline warning card with the re-run affordance).
        }
      },
      error: (err: MaintenanceErrorBody) => {
        this.executing.set(false);
        this.activeRunId.set(null);
        this.pollSub?.unsubscribe();
        this.pollSub = null;
        if (err?.error === 'not_found') {
          this.snackBar.open(
            'The run record was not found — re-run from the status page.',
            'Dismiss',
            { duration: 6000, panelClass: 'error-snackbar' },
          );
        } else {
          this.snackBar.open(
            err?.message ?? 'Polling failed.',
            'Dismiss',
            { duration: 6000, panelClass: 'error-snackbar' },
          );
        }
      },
    });
  }

  dismissError(): void {
    this.service.clearLastError();
  }

  // ── Display helpers (pure; protected for template) ─────────────────────

  /** AM-11 — branch on `destructive:bool` to pick the active flavor. */
  blobCountFor(summary: CheckpointCleanupBlobsSummary): { label: string; value: number } {
    if (summary.destructive) {
      return { label: 'Blobs deleted', value: summary.deleted ?? 0 };
    }
    return { label: 'Would delete (blobs)', value: summary.would_delete_count };
  }

  /** AM-11 — paired byte label. */
  blobBytesFor(summary: CheckpointCleanupBlobsSummary): { label: string; value: number } {
    if (summary.destructive) {
      return { label: 'Bytes freed', value: summary.bytes_freed ?? 0 };
    }
    return { label: 'Would free', value: summary.would_free_bytes };
  }

  /** AM-10 — reason → human label + tone. */
  skippedReasonLabel(reason: string): { label: string; tone: 'safe' | 'limit' | 'error' } {
    if (reason === 'ZERO_REFS_FAIL_SAFE') {
      return { label: 'Fail-safe (no refs)', tone: 'safe' };
    }
    if (reason === 'MAX_REFS_EXCEEDED') {
      return { label: 'Ref cap exceeded', tone: 'limit' };
    }
    if (reason.startsWith('ERROR:')) {
      return { label: `Error: ${reason.slice('ERROR:'.length)}`, tone: 'error' };
    }
    return { label: reason, tone: 'error' };
  }

  /**
   * Item 4 / Item 20 — error code → human label for the inline banner.
   * Curated mapping for codes with stable UX copy; everything else
   * renders verbatim (the raw code string).
   *
   * Fix commission 2026-09-29 — the FE-only `'poll_stale'` sentinel
   * (mapped from a poll-timeout body carrying
   * `details.fe_synthesized_poll_timeout: true`) is REMOVED. The
   * service no longer synthesizes a poll-timeout error — the
   * post-cap backoff phase keeps polling until terminal, so the
   * UI never surfaces a stale-poll dead-end. Iter3: the dead
   * `'poll_stale'` member was also dropped from the
   * `MaintenanceDisplayCode` type (frontend-wide grep: zero
   * remaining producers/consumers) — the type is now a pure
   * display alias of the wire union.
   *
   * Item 20 — `errorLabel()` table lookup via `Partial<Record<...>>`
   * keeps the curated labels in one place; adding a code = adding a
   * tuple entry. Non-curated codes render verbatim (the `??` branch).
   */
  private static readonly ERROR_LABEL_MAP: Partial<Record<string, string>> = {
    internal_error: 'Internal server error',
  };

  errorLabel(code: string): string {
    return CheckpointCleanupComponent.ERROR_LABEL_MAP[code] ?? code;
  }

  /**
   * Item 4 — derive the FE display code from the wire body. With
   * the FE-synthesized poll-timeout marker removed (fix commission
   * 2026-09-29), the marker-based branch into `'poll_stale'` is
   * gone — every body surfaces verbatim. This function is now a
   * thin pass-through over `lastError().error`.
   */
  displayErrorCode(): MaintenanceDisplayCode | '' {
    const err = this.lastError();
    return err ? err.error : '';
  }

  /** AM-6 — interrupted-state render guard. */
  canRerunInterrupted(run: CheckpointCleanupRun): boolean {
    return run.status === 'interrupted';
  }

  /** T6.3 pin target: `formatBytes` source MUST contain `1024` (binary). */
  formatBytes(n: number): string {
    if (typeof n !== 'number' || n < 0 || !Number.isFinite(n)) {
      return '0 B';
    }
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = n;
    let i = 0;
    // Binary divisor — 1024 — pinned by source-grep.
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i++;
    }
    const formatted = value >= 100 || i === 0 ? value.toFixed(0) : value.toFixed(1);
    return `${formatted} ${units[i]}`;
  }

  /** Item 17 — duration formatting constants (single-source). */
  private static readonly MS_PER_SECOND = 1000;
  private static readonly MS_PER_MINUTE = 60_000;

  /** Honest-duration copy: ms → human. "412ms", "1.8s", "2m 14s". */
  formatDuration(ms: number | null | undefined): string {
    if (ms == null || !Number.isFinite(ms) || ms < 0) {
      return '—';
    }
    if (ms < CheckpointCleanupComponent.MS_PER_SECOND) {
      return `${Math.round(ms)}ms`;
    }
    if (ms < CheckpointCleanupComponent.MS_PER_MINUTE) {
      return `${(ms / CheckpointCleanupComponent.MS_PER_SECOND).toFixed(1)}s`;
    }
    const totalSec = Math.floor(ms / CheckpointCleanupComponent.MS_PER_SECOND);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    if (m < 60) {
      return s > 0 ? `${m}m ${s}s` : `${m}m`;
    }
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
  }

  /** ISO timestamp → locale string. `+00:00` and `Z` parse equivalently (A-7). */
  formatTimestamp(iso: string | null | undefined): string {
    if (!iso) {
      return '—';
    }
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  // ── Private helpers ───────────────────────────────────────────────────

  /**
   * v3.2 — three additive projection fields on the dry-run response.
   *
   * Per the amendment's FE display contract: "This run / After this
   * run (run cleanup again) / Combined". Zero components render as
   * "—" (amendment copy). Each helper falls back to `would_free_bytes`
   * for the `now` value (alias-of shape-stability) and treats missing
   * `after`/`total` fields as 0 — v3.1 legacy dry-run payloads render
   * with `after: '—'` and a `total === now`.
   *
   * R-1 ruling — `total === now + after` is COHERENT only with
   * delta-semantics `after` (subset of post-D orphans minus the
   * already-orphan portion). Helpers below are pure; the template
   * renders them inline.
   */

  /** "—" for zero, else human-formatted bytes. This-run number. */
  dryRunProjectionNow(dryRun: CheckpointCleanupDryRun): string {
    const v = dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes ?? 0;
    return this.formatOrDash(v);
  }

  /** "—" for zero, else human-formatted bytes. Follow-up run number. */
  dryRunProjectionAfter(dryRun: CheckpointCleanupDryRun): string {
    const v = dryRun.bytes_reclaimable_after_row_prune ?? 0;
    return this.formatOrDash(v);
  }

  /**
   * "—" for zero, else human-formatted bytes. Sum. Reads the
   * wire-level `bytes_reclaimable_total` when present (exact
   * server sum), otherwise derives `now + after` — same value
   * modulo drift. Pin: schema docstring states total is derived.
   */
  dryRunProjectionTotal(dryRun: CheckpointCleanupDryRun): string {
    const now = dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes ?? 0;
    const after = dryRun.bytes_reclaimable_after_row_prune ?? 0;
    const v = dryRun.bytes_reclaimable_total ?? now + after;
    return this.formatOrDash(v);
  }

  /**
   * `pure` helper — render zero as the literal "—" sentinel rather
   * than "0 B" (amendment: "Zero components render as '—' (skipped).").
   * Negative or non-finite inputs also yield "—" to guard against
   * malformed wire payloads during a delivery-window defect.
   */
  private formatOrDash(v: number): string {
    return typeof v === 'number' && v > 0 && Number.isFinite(v) ? this.formatBytes(v) : '—';
  }

  /**
   * R-4 honesty flag — render the "skipped pairs may understate
   * effectiveness" notice when `skipped[]` is non-empty. Mirrors
   * the existing AM-10 skipped summary on the same data, but
   * lives near the projection so the operator sees it BEFORE
   * clicking execute (this is the consent-time signal).
   */
  dryRunSkippedHonestyActive(dryRun: CheckpointCleanupDryRun): boolean {
    return dryRun.skipped.length > 0;
  }

  /**
   * Sub-copy on never-pruned profiles — `now == 0 && after > 0`
   * means pass 1 frees no blob bytes (Op D will orphan them) and a
   * follow-up run is required for the visible reclaim. The note
   * names the journey explicitly so the operator understands why
   * the "This run" number is "—" even though retention work is
   * obvious.
   */
  isNeverPrunedProfile(dryRun: CheckpointCleanupDryRun): boolean {
    const now = dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes ?? 0;
    const after = dryRun.bytes_reclaimable_after_row_prune ?? 0;
    return now === 0 && after > 0;
  }

  // ── Post-run banner helpers (v3.2) ────────────────────────────────────

  /**
   * Post-run banner visibility (R-5). Renders when:
   *   1. The last execute run succeeded
   *   2. Its summary carries a projection with `after > 0`
   *   3. The user has NOT yet run a fresh dry-run that converged
   *      (`bytes_reclaimable_now == 0` — they've reached pass 2's
   *      claim; nothing more to reclaim)
   *
   * v3.2 B1 — condition (3) is SCOPED by `executedDryRunId`. The
   * hide rule fires when `(anchor === null || dry.run_id !== anchor)
   * && now == 0`. Reading this against the four truth-table rows:
   *
   *   - row (a) live execute + same dry-run: anchor === dry.run_id
   *     → `anchor === null` is false AND `dry.run_id !== anchor` is
   *     false → hide doesn't fire → banner VISIBLE. The pre-execute
   *     dry-run on a never-pruned profile reports `now == 0` here;
   *     without the anchor scope the banner would never appear
   *     (the incident scenario).
   *   - row (b) live execute + fresh converging dry-run:
   *     dry.run_id !== anchor (new id) AND now == 0 → hide fires →
   *     banner HIDDEN. Triggered by clicking "Run again" (banner CTA)
   *     or "Dry-run check" after the execute.
   *   - row (c) seeded session + no user action: anchor is null,
   *     lastDryRun is null → hide doesn't fire → banner VISIBLE
   *     (the dry===null short-circuit covers this).
   *   - row (d) seeded session + fresh converging dry-run: anchor is
   *     null → `anchor === null` is true → hide fires → banner
   *     HIDDEN.
   *
   * The anchor is `performExecute`-owned state — `seedBannerFromStatus`
   * must NOT wipe it (otherwise the post-terminal refreshStatus
   * call would re-anchor null and row (a) regresses). See
   * `seedBannerFromStatus` JSDoc for the contract.
   *
   * The banner is intentionally tied to the run-row's projection
   * echo — no client-only state. Page refreshes (or DAEMON
   * restarts during a poll) still recover the banner from the
   * run summary via `seedBannerFromStatus()` in `refreshStatus()`
   * (B2). Auto rows have no projection block (R-5) so the banner
   * never fires on them.
   */
  showRunAgainBanner(): boolean {
    const run = this.lastExecuteResult();
    if (!run || run.status !== 'succeeded' || !run.summary) {
      return false;
    }
    const after =
      run.summary.projection?.bytes_reclaimable_after_row_prune_at_dry_run ?? 0;
    if (after <= 0) {
      return false;
    }
    // v3.2 B1 + B2 — widened hide rule. Fires when:
    //   (a) `anchor === null` (seeded session: anchor was never
    //       set because no performExecute this session) AND a
    //       dry-run with `now == 0` exists, OR
    //   (b) `dry.run_id !== anchor` (a FRESH dry-run AFTER the
    //       execute, with a different `run_id` than the execute
    //       anchor) AND `now == 0`.
    // In the live execute path, anchor stays as the pre-execute
    // dry-run's `run_id` (the seed does not wipe it), so the
    // pre-execute dry-run is excluded — row (a) stays visible.
    // The `anchor === null` arm covers seeded sessions where
    // there is no live anchor — row (d) hides on convergence.
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

  /** Banner CTA copy source — the projection's after-reclaim bytes. */
  runAgainReclaimBytes(): number {
    const run = this.lastExecuteResult();
    return run?.summary?.projection?.bytes_reclaimable_after_row_prune_at_dry_run ?? 0;
  }

  private buildConfirmMessage(dryRun: CheckpointCleanupDryRun): string {
    // v3.2 — journey copy (amendment VERBATIM). The amendment splits
    // per-run honesty (what THIS run does) from journey honesty
    // (what a follow-up run will reclaim) — the confirm dialog is
    // the consent instrument, so the journey note belongs here too.
    const now = this.formatBytes(dryRun.bytes_reclaimable_now ?? dryRun.would_free_bytes);
    const after = this.formatBytes(
      dryRun.bytes_reclaimable_after_row_prune ?? 0,
    );
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
    if (!err) {
      return;
    }
    if (err.error === 'dry_run_stale' || err.error === 'dry_run_required') {
      this.lastDryRun.set(null); // force re-run
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

  /** AM-13 — kill-switch OFF: render the global banner. */
  isMaintenanceDisabled(): boolean {
    return this.lastError()?.error === 'maintenance_disabled';
  }
}
