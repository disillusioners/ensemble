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
import { MatSnackBar } from '@angular/material/snack-bar';
import { JobService } from '../../services/job.service';
import { ProjectService } from '../../services/project.service';
import { TabStateService } from '../../services/tab-state.service';
import { Job, JobStatus, MissionLiveness, isTerminalStatus } from '../../models/job.model';
import { MissionListResponse, MissionSummary } from '../../models/mission.model';
import { DeferBlockedStatus, DeferBlockIndicator, DeferBlockSeverity, DeferBlockAction, deferBlockIndicator, deferBlockAction } from '../../models/defer-blocked.model';
import { forkJoin, catchError, of } from 'rxjs';
import { JobQueuePanelComponent } from '../job-queue-panel/job-queue-panel.component';

/**
 * Header status indicator that surfaces the live job queue as a
 * SEGMENTED status pill: jobs (running/non-terminal) on the left,
 * live missions (count + pulse dot) on the right, defer-gate warning
 * ⚠ adjacent (unchanged). The whole pill is ONE click target that
 * opens the mat-menu — affordances are NOT split.
 *
 * The button is ``mat-button`` (not icon-button) so the count can
 * render as plain monospace text in the header bar. Clicking opens
 * the menu; clicking a row inside the panel triggers navigation
 * to the underlying instance via ``onJobClick``.
 *
 * Data sources (all on ONE 8s tick — no separate pollers):
 *   - ``JobService.listActiveJobs()``             — running + pending jobs
 *   - ``JobService.listRecentJobs(10)``           — terminal jobs for the
 *     ``Recent`` section of the embedded panel
 *   - ``JobService.listMissions({ limit: 20 })``  — missions list (BE
 *     orders by last_activity desc; the badge derives the count + the
 *     liveness breakdown for the tooltip from this list)
 *   - ``JobService.listDeferBlocked()``           — defer-gate warning
 *     payload for the severity icon beside the badge
 *
 * The former ``JobService.listLiveMissionCount(limit=1)`` round-trip
 * is REPLACED — the segmented pill needs the per-liveness breakdown
 * for its tooltip, so a single ``listMissions({ limit: 20 })`` call
 * serves both the count and the breakdown.
 *
 * All four fire together via ``forkJoin`` on the same 8s tick so the
 * snapshot stays internally consistent. The two additive participants
 * (missions + deferBlocked) carry their own ``catchError`` so a
 * failure (404/503 during BE rollout skew, 500, network) degrades
 * THAT participant to ``null`` without failing the whole ``forkJoin``
 * — the jobs intake keeps flowing. ``null`` missions ⇒ the last known
 * data is RETAINED (never a bare 0/0). ``null`` deferBlocked ⇒ the
 * warning icon hides.
 *
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
  private readonly snackBar = inject(MatSnackBar);

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
   * Last successful missions-list response payload — ``null`` when no
   * fetch has landed yet OR the latest poll's missions leg degraded
   * (the badge then retains the previous good payload so the count
   * never flips back to a false bare 0/0).
   *
   * REPLACES the former ``missionCountRaw: number | null`` — the
   * segmented pill needs the per-liveness breakdown (processing /
   * pending / paused counts) for the tooltip, so we keep the FULL
   * ``MissionSummary[]`` plus the envelope ``total`` here. The
   * former single-number signal did not carry enough detail.
   */
  private readonly missionsPayload = signal<MissionListResponse | null>(null);

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
   *
   * Count leg degraded: when ``total`` is ``null`` on the latest
   * payload (BE degradation contract — §8.2 honesty: "count
   * unavailable" must NOT read as 0), fall back to
   * ``missions.length`` as a defensive filter-aware count.
   */
  readonly liveMissionCount = computed(() => {
    const p = this.missionsPayload();
    if (!p) return 0;
    return p.total ?? p.missions.length;
  });

  /** True when the missions projection reports at least one live mission. */
  readonly hasLiveMissions = computed(() => this.liveMissionCount() > 0);

  /**
   * Raw missions list (latest good payload) — feeds the panel via
   * ``getMissions()`` / signals so the panel can group jobs by
   * mission_id without a second round-trip. Retained across a
   * degraded poll so the panel never flashes empty.
   */
  readonly missionsList = computed<MissionSummary[]>(() => {
    return this.missionsPayload()?.missions ?? [];
  });

  /**
   * Per-liveness breakdown of the current missions list — drives
   * the segmented pill's tooltip ``Live missions: N (processing a,
   * pending b, paused c)`` line.
   */
  readonly liveMissionBreakdown = computed(() => {
    const list = this.missionsList();
    let processing = 0;
    let pending = 0;
    let paused = 0;
    for (const m of list) {
      if (m.liveness === 'processing') processing += 1;
      else if (m.liveness === 'pending') pending += 1;
      else if (m.liveness === 'paused') paused += 1;
    }
    return { processing, pending, paused } as Record<MissionLiveness, number>;
  });

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
   * Pill STATE — drives which template branch renders (segmented /
   * missions-only / idle). Three discrete branches keep the styling
   * clean and the spec straightforward; the underlying numbers flow
   * through ``runningCount`` / ``pendingCount`` / ``liveMissionCount``
   * computeds.
   *
   *   * ``'segmented'`` — jobs present AND live missions present.
   *     Left segment = running/non-terminal count, right = mission
   *     count + pulse dot. Both numbers stay explained.
   *   * ``'missions-only'`` — queue idle (no non-terminal jobs) BUT
   *     live missions exist. Pill shows the right segment only
   *     (pulse + count) — a working leader never reads as system idle.
   *   * ``'idle'`` — both empty. Muted grey.
   */
  readonly pillState = computed<'segmented' | 'missions-only' | 'idle'>(() => {
    if (this.totalNonTerminal() > 0) {
      return this.hasLiveMissions() ? 'segmented' : 'segmented';
    }
    if (this.hasLiveMissions()) return 'missions-only';
    return 'idle';
  });

  /**
   * Jobs segment text — left side of the segmented pill. Format is
   * ``X/Y`` where X = running, Y = total non-terminal (running +
   * pending). Matches the legacy ``displayText`` when jobs are
   * present.
   */
  readonly jobsSegmentText = computed(
    () => `${this.runningCount()}/${this.totalNonTerminal()}`
  );

  /**
   * Missions segment text — right side of the segmented pill. Just
   * the integer N (no ``missions: `` prefix anymore — the pill's
   * segmented layout already conveys "this is the missions count").
   */
  readonly missionsSegmentText = computed(
    () => `${this.liveMissionCount()}`
  );

  /**
   * Tooltip text shown on hover — multi-line breakdown per the brief:
   * ``Running X · Queued Y · Live missions: N (processing a,
   * pending b, paused c) · refreshed Ns ago``. Always present, even
   * when idle, so the user can distinguish a real idle from a
   * transient refresh gap.
   */
  readonly tooltipText = computed(() => {
    const breakdown = this.liveMissionBreakdown();
    return [
      `Running ${this.runningCount()} · Queued ${this.pendingCount()}`,
      `Live missions: ${this.liveMissionCount()} (processing ${breakdown.processing}, pending ${breakdown.pending}, paused ${breakdown.paused})`,
      `refreshed ${this.refreshAgeSeconds()}s ago`,
    ].join('\n');
  });

  /** Seconds since the last successful poll — drives ``refreshed Ns ago``. */
  readonly refreshAgeSeconds = computed(() => {
    if (!this.lastFetchAt()) return 0;
    return Math.max(0, Math.floor((Date.now() - this.lastFetchAt()!) / 1000));
  });

  /** Wall-clock time of the last successful forkJoin — used by ``refreshAgeSeconds``. */
  private readonly lastFetchAt = signal<number | null>(null);

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
   * - ``missions`` — authoritative missions projection
   *   (``GET /api/missions?limit=20`` — REPLACES the former
   *   ``listLiveMissionCount(limit=1)`` call; the segmented pill's
   *   tooltip needs the per-liveness breakdown, which means we have
   *   to fetch the actual rows, not just a count);
   * - ``deferBlocked`` — defer-gate warning payload
   *   (``GET /api/queues/defer-blocked``).
   *
   * The two additive participants carry their own ``catchError`` so a
   * failure (404/503 during BE rollout skew, 500, network) degrades
   * THAT participant to ``null`` without failing the whole
   * ``forkJoin`` — the jobs intake keeps flowing. ``null`` missions ⇒
   * the last known payload is retained (never falsely idle);
   * ``null`` deferBlocked ⇒ the warning icon hides.
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
        .listMissions({ limit: 20 })
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
   * - ``missions === null`` (degraded list / fetch failure) RETAINS
   *   the previous payload — "data unavailable" must not collapse to
   *   a bare 0/0, so the badge never falsely reports an idle system.
   * - ``deferBlocked === null`` hides the warning affordance.
   */
  private applyFetchResults(
    active: Job[],
    recent: Job[],
    missions: MissionListResponse | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    this.activeJobs.set(active);
    this.allRecentJobs.set(recent);
    if (missions !== null) {
      this.missionsPayload.set(missions);
    }
    this.lastFetchAt.set(Date.now());
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
          this.snackBar.open(result.message, 'Close', {
            duration: 3000,
            panelClass: result.terminated ? 'success-snackbar' : 'error-snackbar',
          });
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
          this.snackBar.open(result.message, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
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
