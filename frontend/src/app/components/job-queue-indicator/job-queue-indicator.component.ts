import {
  Component,
  inject,
  signal,
  computed,
  OnInit,
  OnDestroy,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule, MatMenuTrigger } from '@angular/material/menu';
import { JobService } from '../../services/job.service';
import { ProjectService } from '../../services/project.service';
import { TabStateService } from '../../services/tab-state.service';
import { Job, JobStatus } from '../../models/job.model';
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
 */
@Component({
  selector: 'app-job-queue-indicator',
  standalone: true,
  imports: [
    CommonModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
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

  /** Poll interval, in milliseconds. */
  private readonly POLL_INTERVAL_MS = 8000;

  /** Raw active jobs (running + pending) returned by listActiveJobs. */
  private readonly activeJobs = signal<Job[]>([]);

  /** Raw recent jobs (terminal states) returned by listRecentJobs(10). */
  readonly recentJobs = signal<Job[]>([]);

  /**
   * Cached project_id → project name. Rebuilt on init. Keys are
   * strings (project ids) — the ``null`` key variant exists for
   * parity with the panel input type but is unused because the
   * ProjectService only returns real project ids.
   */
  readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  /** Reference to the mat-menu trigger so we can programmatically close it. */
  @ViewChild(MatMenuTrigger) menuTrigger?: MatMenuTrigger;

  /** Running (processing/active) job count — the X in "X/Y". */
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

  /** Formatted indicator text: "X/Y" where X=running, Y=total non-terminal. */
  readonly displayText = computed(
    () => `${this.runningCount()}/${this.totalNonTerminal()}`
  );

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
    this.projectService.listProjects().subscribe({
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
   * recent list is already trimmed server-side (``limit=10``) but
   * we sort + slice here as defence-in-depth and to enforce a
   * deterministic order (newest first by ``completed_at`` falling
   * back to ``created_at``).
   */
  private fetchJobs(): void {
    forkJoin({
      active: this.jobService.listActiveJobs(),
      recent: this.jobService.listRecentJobs(10),
    }).subscribe({
      next: ({ active, recent }) => {
        this.activeJobs.set(active);
        const sortedRecent = [...recent]
          .sort((a, b) => {
            const aT = a.completed_at ?? a.created_at;
            const bT = b.completed_at ?? b.created_at;
            return bT.localeCompare(aT);
          })
          .slice(0, 10);
        this.recentJobs.set(sortedRecent);
      },
      error: (err) => {
        console.error('[JobQueueIndicator] Failed to fetch jobs:', err);
        this.activeJobs.set([]);
        this.recentJobs.set([]);
      }
    });
  }

  /**
   * Handle a job click from the embedded panel. Opens the right tab
   * (or switches to the ``all`` tab for unassigned jobs), closes
   * the dropdown, then navigates to the instance view.
   */
  onJobClick(job: Job): void {
    if (job.project_id) {
      this.tabStateService.addTab({
        project_id: job.project_id,
        name: job.project_id.slice(0, 8),
      });
    } else {
      this.tabStateService.setActiveTab('all');
    }
    this.menuTrigger?.closeMenu();
    this.router.navigate([
      '/projects',
      job.project_id || 'all',
      'instances',
      job.instance_id,
    ]);
  }

  /**
   * Fallback label for projects whose name we couldn't resolve:
   * first 8 chars of the id followed by an ellipsis if the id is
   * longer. Matches the notification-bell truncation convention
   * used elsewhere in the header.
   */
  shortenId(id: string): string {
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }
}

/**
 * Defensive status predicates. ``processing``/``pending`` are the
 * canonical names from the ``JobStatus`` enum, but the backend's
 * internal lifecycle still uses ``active``/``queued`` in some
 * paths (and the active-jobs endpoint filters on the latter).
 * Accepting both keeps the indicator robust if the backend ever
 * leaks those names through.
 */
function isRunningStatus(status: JobStatus): boolean {
  return status === 'processing' || (status as string) === 'active';
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
