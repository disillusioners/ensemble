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
import { Job, JobStatus, isLiveMissionLiveness } from '../../models/job.model';
import { forkJoin } from 'rxjs';
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
 * Data sources:
 *   - ``JobService.listActiveJobs()``   — running + pending jobs
 *   - ``JobService.listRecentJobs(10)`` — terminal jobs for the
 *     ``Recent`` section of the embedded panel
 *
 * Both requests fire together via ``forkJoin`` on the same 8s tick
 * so the snapshot stays internally consistent. Project names are
 * resolved once on init via ``ProjectService.listProjects()`` and
 * cached in ``projectNameMap`` for the lifetime of the component.
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

  // ── Fix C read-model split (§8.2) — mission awareness ───────────────

  /**
   * Distinct live-mission instance ids, derived from data this
   * component ALREADY polls (no new endpoints, no extra requests):
   *
   * A mirror row (``job_type === 'message'``) whose
   * ``mission_liveness`` is live (pending/processing/paused) proves
   * its parent mission is still working — even when the mirror's own
   * receipt status is terminal (handled at T0). This is exactly the
   * "0/0 badge while a mission leader is visibly working" case: the
   * leader produces only terminal receipts, so the intake count
   * reads 0/0 while real work is ongoing.
   *
   * ``mission_liveness`` is computed read-time by the backend
   * resolver, so even older terminal mirrors in the recent window
   * carry the CURRENT instance status — a leader that finished
   * reads settled and stops counting.
   *
   * Sources: the active list (defensive — mirrors are terminal at
   * T0, but the scan is cheap) + the recent terminal window. Rows
   * are de-duplicated by ``instance_id`` (many receipts per
   * mission, one mission); a null instance_id falls back to the
   * job id so the row still counts rather than silently vanishing.
   */
  readonly liveMissionIds = computed(() => {
    const ids = new Set<string>();
    for (const j of [...this.activeJobs(), ...this.recentJobs()]) {
      if (j.job_type === 'message' && j.mission_liveness && isLiveMissionLiveness(j.mission_liveness)) {
        // instance_id is `string | null` (required, not optional); the job_id fallback prevents null instance_ids from colliding in the Set.
        ids.add(j.instance_id ?? j.job_id);
      }
    }
    return ids;
  });

  /** Number of distinct live missions behind handled receipts. */
  readonly liveMissionCount = computed(() => this.liveMissionIds().size);

  /** True when at least one parent mission is still working. */
  readonly hasLiveMissions = computed(() => this.liveMissionCount() > 0);

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
   * a live-missions line whenever the receipt window proves a
   * parent mission is still working. Both numbers are always
   * explained: jobs (Running/Pending) and missions (Live missions).
   */
  readonly tooltipText = computed(() => {
    const base = `Running: ${this.runningCount()} / Pending: ${this.pendingCount()}`;
    if (!this.hasLiveMissions()) {
      return base;
    }
    const plural = this.liveMissionCount() === 1 ? '' : 's';
    return (
      `${base} · Live missions: ${this.liveMissionCount()} ` +
      `(message${plural === '' ? '' : 's'} handled; parent mission${plural} still working)`
    );
  });

  /** Running-only subset — passed to the embedded panel. */
  readonly runningJobs = computed(() =>
    this.activeJobs().filter((j) => isRunningStatus(j.status))
  );

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.loadProjectNames();
    this.fetchJobs();
    this.pollHandle = setInterval(() => this.fetchJobs(), this.POLL_INTERVAL_MS);
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
   * Fetch active + recent jobs in parallel via ``forkJoin`` so the
   * active and recent snapshots stay consistent on each tick. The
   * raw recent payload is stored in ``allRecentJobs`` and a
   * derived ``recentJobs`` computed filters/sorts/slices it for
   * the panel — see the field docs for why the public surface is
   * defensive.
   *
   * Errors propagate through the single ``forkJoin`` error handler
   * here (the underlying service methods no longer swallow
   * failures) so we can log and reset both signals to ``[]``.
   */
  private fetchJobs(): void {
    forkJoin({
      active: this.jobService.listActiveJobs(),
      recent: this.jobService.listRecentJobs(10),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ active, recent }) => {
          this.activeJobs.set(active);
          this.allRecentJobs.set(recent);
        },
        error: (err) => {
          console.error('[JobQueueIndicator] Failed to fetch jobs:', err);
          this.activeJobs.set([]);
          this.allRecentJobs.set([]);
        }
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

function isTerminalStatus(status: JobStatus): boolean {
  return (
    status === 'completed' ||
    status === 'failed' ||
    status === 'cancelled' ||
    status === 'dead_letter'
  );
}
