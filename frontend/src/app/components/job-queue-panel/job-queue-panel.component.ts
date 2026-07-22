import { Component, input, output, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Job, JobStatus } from '../../models/job.model';

const MAX_RECENT_JOBS = 10;

/**
 * Presentational panel that surfaces the current job queue state in
 * two sections: jobs currently running and recently completed/failed/
 * cancelled jobs. This is a DUMB component — all data is pushed in
 * via inputs and click events flow out via the ``jobClick`` output.
 *
 * The parent component is responsible for fetching jobs, slicing
 * recent activity, and resolving any project/instance name maps. We
 * additionally cap the recent list to a small number for safety.
 *
 * Styling mirrors the notification-bell dropdown (dark slate panel,
 * 440px max width, monospace badges, status-coloured left border).
 */
@Component({
  selector: 'app-job-queue-panel',
  standalone: true,
  imports: [
    CommonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  templateUrl: './job-queue-panel.component.html',
  styleUrl: './job-queue-panel.component.scss',
})
export class JobQueuePanelComponent {
  /** Currently active/processing jobs. */
  runningJobs = input<Job[]>([]);

  /** Recently completed/failed/cancelled jobs (already trimmed by parent). */
  recentJobs = input<Job[]>([]);

  /** project_id → display name. Keys may be null for unassigned jobs. */
  projectNameMap = input<Map<string | null, string>>(new Map());

  /** instance_id → display title. Optional; falls back to job metadata. */
  instanceTitleMap = input<Map<string, string> | undefined>(undefined);

  /** Emitted when the user clicks any job row. */
  jobClick = output<Job>();

  /** Capped list of recent jobs (defence-in-depth slice). */
  recentCapped = computed(() => this.recentJobs().slice(0, MAX_RECENT_JOBS));

  /** True when both running and recent lists are empty. */
  isEmpty = computed(
    () => this.runningJobs().length === 0 && this.recentCapped().length === 0,
  );

  /** Convenience running count used in the header. */
  runningCount = computed(() => this.runningJobs().length);

  /**
   * Resolves the best available title for a job. Priority chain:
   * 1. instanceTitleMap lookup (when provided)
   * 2. job_metadata.instance_name
   * 3. agent_id
   * 4. shortenId of instance_id (or job_id) as a last resort
   */
  resolveTitle(job: Job): string {
    const map = this.instanceTitleMap();
    if (map && job.instance_id && map.has(job.instance_id)) {
      return map.get(job.instance_id)!;
    }

    const meta = job.job_metadata;
    if (meta && typeof meta === 'object' && meta['instance_name']) {
      return String(meta['instance_name']);
    }

    if (job.agent_id) {
      return job.agent_id;
    }

    return this.shortenId(job.instance_id ?? job.job_id);
  }

  /** Project display label — falls back to a shortened id when unknown. */
  projectLabel(job: Job): string {
    const id = job.project_id;
    if (id === null || id === undefined) return '—';
    return this.projectNameMap().get(id) ?? this.shortenId(id);
  }

  /**
   * Truncate an id to its first 8 characters followed by an ellipsis.
   * Matches the convention used by ``job-queue-indicator`` and
   * ``notification-bell`` so the same id looks consistent across the
   * header. Returns an em-dash for null/empty input.
   */
  shortenId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  /**
   * Human-readable "X ago" formatter for completed/created timestamps.
   * Mirrors the logic in ``notification-bell.component.ts`` and
   * ``skill-card.component.ts`` — no shared util exists, so this
   * keeps the duplication intentional and local.
   */
  timeAgo(dateString: string | null | undefined): string {
    if (!dateString) return '';
    const now = new Date();
    const date = new Date(dateString);
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString();
  }

  /** Material icon name for a terminal job status. */
  getStatusIcon(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'failed':
        return 'error';
      case 'cancelled':
        return 'cancel';
      case 'dead_letter':
        return 'inventory_2';
      default:
        return 'info';
    }
  }

  /** Accent color for a terminal job status (matches notification-bell). */
  getStatusColor(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return '#10b981';
      case 'failed':
        return '#f43f5e';
      case 'cancelled':
        return '#f59e0b';
      case 'dead_letter':
        return '#7c3aed';
      default:
        return '#94a3b8';
    }
  }

  /** Emits the clicked job up to the parent for navigation. */
  onRowClick(job: Job): void {
    this.jobClick.emit(job);
  }
}
