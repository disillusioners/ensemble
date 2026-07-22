import { Component, inject, signal, computed, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatBadgeModule } from '@angular/material/badge';
import { MatTooltipModule } from '@angular/material/tooltip';
import { JobService } from '../../services/job.service';
import { ProjectService } from '../../services/project.service';
import { Job, JobStatus } from '../../models/job.model';

/**
 * Header status indicator that surfaces the number of queued + active
 * jobs across all projects. Click-to-navigate jumps to the Jobs page.
 *
 * Polls GET /api/jobs?status=queued,active every 8 seconds and groups
 * jobs by project_id for the tooltip breakdown. Project names are
 * resolved via ProjectService and cached for the component lifetime.
 */
@Component({
  selector: 'app-job-queue-indicator',
  standalone: true,
  imports: [CommonModule, MatIconModule, MatButtonModule, MatBadgeModule, MatTooltipModule],
  templateUrl: './job-queue-indicator.component.html',
  styleUrl: './job-queue-indicator.component.scss'
})
export class JobQueueIndicatorComponent implements OnInit, OnDestroy {
  private readonly jobService = inject(JobService);
  private readonly projectService = inject(ProjectService);
  private readonly router = inject(Router);

  /** Poll interval, in milliseconds. */
  private readonly POLL_INTERVAL_MS = 8000;

  /** Raw jobs currently in queued/active state. */
  private readonly jobs = signal<Job[]>([]);

  /** Cached project_id → project name. Rebuilt on init. */
  private readonly projectNameMap = signal<Map<string, string>>(new Map());

  /** Computed total count (queued + active). */
  readonly jobCount = computed(() => this.jobs().length);

  /** Whether the badge should be visible (count > 0). */
  readonly hasJobs = computed(() => this.jobCount() > 0);

  /**
   * Per-project breakdown rendered in the tooltip. Each entry is a
   * pre-formatted line so the template can render a simple list
   * without re-computing on every change-detection cycle.
   */
  readonly tooltipLines = computed<string[]>(() => {
    const grouped = this.groupByProject(this.jobs());
    if (grouped.size === 0) {
      return ['No active jobs'];
    }
    const nameMap = this.projectNameMap();
    const lines: string[] = [];
    // Stable order: sort by project_id for deterministic output.
    const sortedKeys = Array.from(grouped.keys()).sort();
    for (const projectId of sortedKeys) {
      const counts = grouped.get(projectId)!;
      const running = counts.running;
      const pending = counts.pending;
      const name = nameMap.get(projectId) ?? this.shortenId(projectId);
      const parts: string[] = [];
      if (running > 0) parts.push(`${running} running`);
      if (pending > 0) parts.push(`${pending} pending`);
      lines.push(`${name}: ${parts.join(', ')}`);
    }
    return lines;
  });

  /** Joined tooltip text for the matTooltip binding. */
  readonly tooltipText = computed(() => this.tooltipLines().join('\n'));

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
   * name for the tooltip. Failures are non-fatal: the map stays
   * empty and we fall back to the shortened id everywhere.
   */
  private loadProjectNames(): void {
    this.projectService.listProjects().subscribe({
      next: (response) => {
        const map = new Map<string, string>();
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
   * Fetch queued + active jobs. On failure, reset to empty so the
   * badge hides and the tooltip shows "No active jobs".
   */
  private fetchJobs(): void {
    this.jobService.listActiveJobs().subscribe({
      next: (jobs) => {
        this.jobs.set(jobs);
      },
      error: (err) => {
        console.error('[JobQueueIndicator] Failed to fetch active jobs:', err);
        this.jobs.set([]);
      }
    });
  }

  /**
   * Group jobs by project_id, counting running (processing) and
   * pending (pending) jobs separately. Jobs without a project_id
   * are bucketed under the literal key ``'__unassigned__'``.
   */
  private groupByProject(jobs: Job[]): Map<string, { running: number; pending: number }> {
    const groups = new Map<string, { running: number; pending: number }>();
    for (const job of jobs) {
      const key = job.project_id ?? '__unassigned__';
      const bucket = groups.get(key) ?? { running: 0, pending: 0 };
      if (this.isRunningStatus(job.status)) {
        bucket.running += 1;
      } else if (this.isPendingStatus(job.status)) {
        bucket.pending += 1;
      }
      groups.set(key, bucket);
    }
    return groups;
  }

  private isRunningStatus(status: JobStatus): boolean {
    return status === 'processing';
  }

  private isPendingStatus(status: JobStatus): boolean {
    return status === 'pending';
  }

  /**
   * Fallback label for projects whose name we couldn't resolve:
   * first 8 chars of the project_id followed by an ellipsis if
   * the id is longer. Matches the notification-bell truncation
   * convention used elsewhere in the header.
   */
  private shortenId(id: string): string {
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  onClick(): void {
    this.router.navigateByUrl('/jobs');
  }
}
