import {
  Component,
  DestroyRef,
  inject,
  signal,
  computed,
  OnInit,
  OnDestroy,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule, MatMenuTrigger } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { JobService } from '../../services/job.service';
import { ProjectService } from '../../services/project.service';
import { TabStateService } from '../../services/tab-state.service';
import { Job, JobStatus, isTerminalStatus } from '../../models/job.model';
import { DeferBlockedStatus, DeferBlockIndicator, DeferBlockSeverity, DeferBlockAction, deferBlockIndicator, deferBlockAction } from '../../models/defer-blocked.model';
import { forkJoin, catchError, of } from 'rxjs';
import { JobQueuePanelComponent } from '../job-queue-panel/job-queue-panel.component';

/**
 * Header status indicator that surfaces the live job queue as
 * ``X/Y`` (running / total non-terminal) and exposes a Material
 * dropdown with the full ``JobQueuePanelComponent`` embedded.
 *
 * The button is ``mat-button`` (not icon-button) so the count can
 * render as plain monospace text in the header bar. Clicking opens
 * the menu; clicking a row inside the panel triggers navigation
 * to the underlying instance via ``onJobClick``.
 *
 * Data sources (all on ONE 8s tick — no separate pollers):
 *   - ``JobService.listActiveJobs()``        — running + pending jobs
 *   - ``JobService.listRecentJobs(10)``      — terminal jobs for the
 *     ``Recent`` section of the embedded panel
 *   - ``JobService.listLiveMissionCount()``  — authoritative
 *     ``GET /api/missions`` live count (the badge's N)
 *   - ``JobService.listDeferBlocked()``      — defer-gate warning
 *     payload for the severity icon beside the badge
 *
 * All four fire together via ``forkJoin`` on the same 8s tick so the
 * snapshot stays internally consistent. The two additive participants
 * isolate their own errors (degrade to ``null``) so a rollout-skew
 * 404 on ``/api/queues/defer-blocked`` can never kill the jobs intake.
 * Project names are resolved once on init via
 * ``ProjectService.listProjects()`` and cached in ``projectNameMap``
 * for the lifetime of the component.
 *
 * Lifecycle: all subscriptions are tied to ``DestroyRef`` via
 * ``takeUntilDestroyed`` so polling stops when the component is
 * torn down — the indicator lives in the header which may be
 * destroyed during navigation.
 */
@Component({
  selector: 'app-job-queue-indicator',
  standalone: true,
  imports: [
    CommonModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    MatTooltipModule,
    JobQueuePanelComponent,
  ],
  templateUrl: './job-queue-indicator.component.html',
  styleUrl: './job-queue-indicator.component.scss'
})
export class JobQueueIndicatorComponent implements OnInit, OnDestroy {
  private readonly jobService = inject(JobService);
  private readonly projectService = inject(ProjectService);
  private readonly tabStateService = inject(TabStateService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  /** Poll interval, in milliseconds. */
  private readonly POLL_INTERVAL_MS = 8000;

  /** Raw active jobs (running + paused + pending) returned by listActiveJobs. */
  private readonly activeJobs = signal<Job[]>([]);

  /**
   * Raw recent jobs returned by ``listRecentJobs(10)`` — defensive
   * for now, but downstream consumers should always read
   * ``recentJobs`` (the public computed) so non-terminal statuses
   * are filtered out.
   */
  private readonly allRecentJobs = signal<Job[]>([]);

  /**
   * Public recent jobs — terminal-only subset of ``allRecentJobs``,
   * deterministically sorted (newest ``completed_at`` first,
   * falling back to ``created_at``) and capped at 10. Using
   * ``isTerminalStatus`` here keeps the public surface safe even
   * if the backend ever leaks a non-terminal status into the
   * recent endpoint.
   */
  readonly recentJobs = computed<Job[]>(() =>
    this.allRecentJobs()
      .filter((j) => isTerminalStatus(j.status))
      .sort((a, b) => {
        const aT = a.completed_at ?? a.created_at;
        const bT = b.completed_at ?? b.created_at;
        return bT.localeCompare(aT);
      })
      .slice(0, 10)
  );

  /**
   * Cached project_id → project name. Rebuilt on init. Keys are
   * strings (project ids) — the ``null`` key variant exists for
   * parity with the panel input type but is unused because the
   * ProjectService only returns real project ids.
   */
  readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  /** Reference to the mat-menu trigger so we can programmatically close it. */
  @ViewChild(MatMenuTrigger) menuTrigger?: MatMenuTrigger;

  /** Running (processing/paused/active) job count — the X in "X/Y". */
  readonly runningCount = computed(
    () => this.activeJobs().filter((j) => isRunningStatus(j.status)).length
  );

  /** Pending (pending/queued) job count. */
  readonly pendingCount = computed(
    () => this.activeJobs().filter((j) => isPendingStatus(j.status)).length
  );

  /** Total non-terminal jobs (running + pending) — the Y in "X/Y". */
  readonly totalNonTerminal = computed(
    () => this.runningCount() + this.pendingCount()
  );

  /** Idle state — drives the muted styling on the button. */
  readonly isIdle = computed(() => this.totalNonTerminal() === 0);

  // ── Mission awareness — sourced from the authoritative projection ───

  /**
   * Live-mission count as reported by ``GET /api/missions``
   * (``liveness=processing,pending,paused``) — the authoritative
   * missions projection. ``null`` = count unavailable (degraded count
   * leg or fetch failure) and is deliberately NOT rendered as 0: the
   * last known count is RETAINED so a transient missions failure can
   * never flip a working system's badge back to a false "bare 0/0".
   */
  private readonly missionCountRaw = signal<number | null>(null);

  /**
   * Distinct live missions, from the missions projection this
   * component polls alongside its job intake (same 8s tick, no
   * separate poller).
   *
   * REPLACES the former receipt-window derivation
   * (``liveMissionIds(active + recent)``): settled tokens that never
   * reached the receipt intake made that N read 0 while a leader
   * mission was visibly working. ``/api/missions`` is correct and
   * authoritative — one mission per instance, liveness-filtered
   * server-side.
   */
  readonly liveMissionCount = computed(() => this.missionCountRaw() ?? 0);

  /** True when the missions projection reports at least one live mission. */
  readonly hasLiveMissions = computed(() => this.liveMissionCount() > 0);

  /**
   * Defer-gate warning affordance, derived via the pure
   * ``deferBlockIndicator`` model helper. ``null`` = no render (zero
   * pending defer jobs, or the endpoint degraded/404 during rollout
   * skew — the icon hides silently).
   */
  readonly deferBlockWarning = signal<DeferBlockIndicator | null>(null);

  /**
   * Raw defer-blocked payload from the latest poll — the input the
   * WS4 holder-action derivation (``deferBlockAction``) needs. The
   * derived indicator carries only severity/tooltip; the actions
   * need the actionable holder's identity.
   */
  private readonly deferBlockedPayload = signal<DeferBlockedStatus | null>(null);

  /**
   * WS4 holder actions for the warning affordance — ``null`` = no
   * action offered (no payload, zero pending defer jobs, or no
   * instance-backed actionable holder). Pure derivation via the
   * model helper (house convention: components stay thin computeds
   * over model helpers).
   */
  readonly deferBlockActionTarget = computed<DeferBlockAction | null>(() =>
    deferBlockAction(this.deferBlockedPayload())
  );

  /** True while a holder action is in flight (buttons disabled). */
  readonly holderActionInProgress = signal(false);

  /** Material icon name per severity — presentation-only mapping. */
  private static readonly DEFER_BLOCK_ICONS: Record<DeferBlockSeverity, string> = {
    amber: 'warning',
    info: 'info',
    red: 'error',
  };

  /** Icon shown in the warning affordance ('' when hidden). */
  readonly deferBlockIcon = computed(() => {
    const warning = this.deferBlockWarning();
    return warning ? JobQueueIndicatorComponent.DEFER_BLOCK_ICONS[warning.severity] : '';
  });

  /**
   * The badge shows system activity even when the intake queue is
   * empty: jobs present → the classic ``X/Y``; queue empty but live
   * missions exist → ``missions: N`` so a working leader never reads
   * as a bare "0/0 = system idle"; both empty → ``0/0`` idle.
   */
  readonly displayText = computed(() => {
    if (this.totalNonTerminal() === 0 && this.hasLiveMissions()) {
      return `missions: ${this.liveMissionCount()}`;
    }
    return `${this.runningCount()}/${this.totalNonTerminal()}`;
  });

  /**
   * Tooltip text shown on hover — exposes the raw counts so the
   * user can distinguish "all running" from "all pending" without
   * opening the dropdown. Format: ``Running: X / Pending: Y``, plus
   * a live-missions line whenever the missions projection reports
   * live work. Both numbers are always explained: jobs
   * (Running/Pending) and missions (Live missions).
   */
  readonly tooltipText = computed(() => {
    const base = `Running: ${this.runningCount()} / Pending: ${this.pendingCount()}`;
    if (!this.hasLiveMissions()) {
      return base;
    }
    return `${base} · Live missions: ${this.liveMissionCount()} (from missions projection)`;
  });

  /** Running-only subset — passed to the embedded panel. */
  readonly runningJobs = computed(() =>
    this.activeJobs().filter((j) => isRunningStatus(j.status))
  );

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.loadProjectNames();
    this.fetchBadgeSignals();
    this.pollHandle = setInterval(() => this.fetchBadgeSignals(), this.POLL_INTERVAL_MS);
  }

  ngOnDestroy(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /**
   * Fetch the project list once on init so we can map project_id →
   * name for the embedded panel. Failures are non-fatal: the map
   * stays empty and the panel falls back to shortened ids.
   */
  private loadProjectNames(): void {
    this.projectService.listProjects().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (response) => {
        const map = new Map<string | null, string>();
        for (const project of response.projects) {
          map.set(project.project_id, project.name);
        }
        this.projectNameMap.set(map);
      },
      error: (err) => {
        console.error('[JobQueueIndicator] Failed to load projects:', err);
      }
    });
  }

  /**
   * Fetch every header badge signal in ONE parallel ``forkJoin`` on
   * the same 8s tick — the name says "badge signals" because this is
   * THREE families, not just jobs (the pre-round-1 ``fetchJobs``
   * name understated it):
   *
   * - jobs intake — active + recent (``X/Y`` + the Recent section);
   * - ``missions`` — authoritative live-mission count
   *   (``GET /api/missions?liveness=processing,pending,paused``);
   * - ``deferBlocked`` — defer-gate warning payload
   *   (``GET /api/queues/defer-blocked``).
   *
   * The two additive participants carry their own ``catchError`` so a
   * failure (404/503 during BE rollout skew, 500, network) degrades
   * THAT participant to ``null`` without failing the whole
   * ``forkJoin`` — the jobs intake keeps flowing. ``null`` missions ⇒
   * the last known count is retained (never falsely idle); ``null``
   * deferBlocked ⇒ the warning icon hides.
   *
   * The raw recent payload is stored in ``allRecentJobs`` and a
   * derived ``recentJobs`` computed filters/sorts/slices it for
   * the panel — see the field docs for why the public surface is
   * defensive.
   *
   * Active/recent errors still propagate to the single ``forkJoin``
   * error handler here (those service methods no longer swallow
   * failures) so we can log and reset both job signals to ``[]``.
   */
  private fetchBadgeSignals(): void {
    forkJoin({
      active: this.jobService.listActiveJobs(),
      recent: this.jobService.listRecentJobs(10),
      missions: this.jobService
        .listLiveMissionCount()
        .pipe(catchError(() => of(null))),
      deferBlocked: this.jobService
        .listDeferBlocked()
        .pipe(catchError(() => of(null))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ active, recent, missions, deferBlocked }) =>
          this.applyFetchResults(active, recent, missions, deferBlocked),
        error: (err) => {
          console.error('[JobQueueIndicator] Failed to fetch badge signals:', err);
          this.activeJobs.set([]);
          this.allRecentJobs.set([]);
        }
      });
  }

  /**
   * Apply one poll tick's results to the component signals. Kept as
   * its own method so the logic-mirror spec can replicate it 1:1
   * with mocked service payloads.
   *
   * - jobs (active + recent) are stored verbatim;
   * - ``missions === null`` (degraded count leg / fetch failure)
   *   RETAINS the previous count — "count unavailable" must not read
   *   as 0, so the badge never falsely reports an idle system;
   * - ``deferBlocked === null`` hides the warning affordance.
   */
  private applyFetchResults(
    active: Job[],
    recent: Job[],
    missions: number | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    this.activeJobs.set(active);
    this.allRecentJobs.set(recent);
    if (missions !== null) {
      this.missionCountRaw.set(missions);
    }
    this.deferBlockedPayload.set(deferBlocked);
    this.deferBlockWarning.set(
      deferBlocked === null ? null : deferBlockIndicator(deferBlocked)
    );
  }

  /**
   * WS4: force-complete the actionable stalled holder.
   *
   * The button is disabled unless the derived action says the holder
   * is ``stalled`` (mirrors-only) — the SERVER re-verifies via the
   * canonical probe at execution time, so a stale-UI click on a
   * since-gone-live holder is still refused safely (200 with
   * ``terminated=false``).
   */
  onForceCompleteHolder(): void {
    const target = this.deferBlockActionTarget();
    if (!target || !target.forceCompleteAllowed || this.holderActionInProgress()) {
      return;
    }
    this.holderActionInProgress.set(true);
    this.jobService
      .forceCompleteDeferHolder(target.holder.instance_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.holderActionInProgress.set(false);
          console.log(
            '[JobQueueIndicator] force-complete:',
            result.terminated ? 'terminated' : 'refused by server guard',
            result.message
          );
          this.fetchBadgeSignals();
        },
        error: (err) => {
          this.holderActionInProgress.set(false);
          console.error('[JobQueueIndicator] force-complete failed:', err);
        },
      });
  }

  /**
   * WS4: re-send the actionable holder's queued defer messages as
   * foreground jobs (cancel + re-enqueue server-side).
   */
  onResendDeferredForeground(): void {
    const target = this.deferBlockActionTarget();
    if (!target || this.holderActionInProgress()) {
      return;
    }
    this.holderActionInProgress.set(true);
    this.jobService
      .resendDeferredForeground(target.holder.instance_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.holderActionInProgress.set(false);
          console.log(
            '[JobQueueIndicator] resend-foreground:',
            result.cancelled_defer_jobs,
            'cancelled,',
            result.resend_results.filter((r) => r.job_id).length,
            're-sent'
          );
          this.fetchBadgeSignals();
        },
        error: (err) => {
          this.holderActionInProgress.set(false);
          console.error('[JobQueueIndicator] resend-foreground failed:', err);
        },
      });
  }

  /**
   * Handle a job click from the embedded panel.
   *
   * Flow:
   *   1. Close the dropdown so the menu surface disappears before
   *      we mutate tab/route state.
   *   2. Open the project tab (resolved via ``projectNameMap``
   *      with a first-8-chars fallback) — or switch to the ``all``
   *      tab if the job is unassigned.
   *   3. Navigate to the specific instance when ``instance_id`` is
   *      truthy, otherwise to the project/all instances list with
   *      no null trailing segment.
   */
  onJobClick(job: Job): void {
    this.menuTrigger?.closeMenu();

    const projectKey = job.project_id || 'all';
    if (job.project_id) {
      const name =
        this.projectNameMap().get(job.project_id) ?? job.project_id.slice(0, 8);
      this.tabStateService.addTab({ project_id: job.project_id, name });
    } else {
      this.tabStateService.setActiveTab('all');
    }

    const navigateTo: (string | null)[] = job.instance_id
      ? ['/projects', projectKey, 'instances', job.instance_id]
      : ['/projects', projectKey, 'instances'];
    this.router.navigate(navigateTo);
  }
}

/**
 * Defensive status predicates. ``processing``/``pending`` are the
 * canonical names from the ``JobStatus`` enum, but the backend's
 * internal lifecycle still uses ``active``/``queued`` in some
 * paths (and the active-jobs endpoint filters on the latter).
 * Accepting both keeps the indicator robust if the backend ever
 * leaks those names through.
 *
 * ``paused`` is treated as running here: the backend classifies
 * it as non-terminal and the Jobs UI surfaces paused rows in the
 * active queue. Counting it as "running" keeps the header badge
 * in sync with the underlying queue state.
 *
 * Terminal classification is NOT redefined here: the canonical
 * ``isTerminalStatus`` (models/job.model.ts) is imported directly —
 * the former module-private copy predated the M3 ``settled`` token
 * and silently misclassified settled receipts as non-terminal.
 */
function isRunningStatus(status: JobStatus): boolean {
  return (
    status === 'processing' ||
    status === 'paused' ||
    (status as string) === 'active'
  );
}

function isPendingStatus(status: JobStatus): boolean {
  return status === 'pending' || (status as string) === 'queued';
}
