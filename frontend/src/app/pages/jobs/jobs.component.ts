import { Component, signal, computed, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { Subscription, switchMap, of, catchError, tap } from 'rxjs';
import { JobService } from '../../services/job.service';
import { JobSseService } from '../../services/job-sse.service';
import { ProjectService } from '../../services/project.service';
import { TabStateService } from '../../services/tab-state.service';
import { QueueService } from '../../services/queue.service';
import { ApiService } from '../../services/api.service';
import { WorkService } from '../../services/work.service';
import { JobCardComponent } from '../../components/job-card/job-card.component';
import { JobDetailDrawerComponent } from '../../components/job-detail-drawer/job-detail-drawer.component';
import { JobCreateDialogComponent, JobCreateDialogResult } from '../../components/job-create-dialog/job-create-dialog.component';
import { QueueListComponent } from '../../components/queue-list/queue-list.component';
import { SearchableSelectComponent } from '../../components';
import { SystemCleanupConfirmDialogComponent } from '../../components/system-cleanup-confirm-dialog/system-cleanup-confirm-dialog.component';
import { Job, JobFilters, JobStatus, JobSource, JobEventPayload, isTerminalStatus } from '../../models/job.model';
import { JobQueue } from '../../models/job-queue.model';
import { Project } from '../../models/project.model';
import { Agent } from '../../models';
import { Work } from '../../models/work.model';

/**
 * Top-level view mode for the Jobs page (Phase 4 — Virtual Job
 * Management Surface).
 *
 * * ``'queues'``   — the legacy "Queues" view: queue sidebar on the
 *   left, jobs filtered by selected queue on the right. Backed by
 *   ``JobService``.
 * * ``'all-work'`` — the unified work list: queue sidebar still
 *   visible but inactive, main pane shows ALL work records (jobs +
 *   turns + reports) backed by ``WorkService``. The kind chip on
 *   each card tells the user which backing table the row came from.
 */
export type JobsViewMode = 'queues' | 'all-work';

@Component({
  selector: 'app-jobs',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatSidenavModule,
    MatSnackBarModule,
    MatDialogModule,
    MatTooltipModule,
    MatCheckboxModule,
    JobCardComponent,
    JobDetailDrawerComponent,
    QueueListComponent,
    SearchableSelectComponent
  ],
  templateUrl: './jobs.component.html',
  styleUrl: './jobs.component.scss'
})
export class JobsComponent implements OnInit, OnDestroy {
  private readonly router = inject(Router);
  private readonly jobService = inject(JobService);
  private readonly jobSseService = inject(JobSseService);
  private readonly projectService = inject(ProjectService);
  private readonly tabStateService = inject(TabStateService);
  private readonly queueService = inject(QueueService);
  private readonly api = inject(ApiService);
  private readonly workService = inject(WorkService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);

  private readonly STORAGE_KEY = 'job-page-selected-project';
  private readonly VIEW_MODE_KEY = 'job-page-view-mode';
  private refreshInterval: ReturnType<typeof setInterval> | null = null;
  private sseSubscription: Subscription | null = null;
  private projectRestored = false;

  // Signals for state
  readonly jobs = signal<Job[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly agents = signal<Agent[]>([]);
  readonly selectedJob = signal<Job | null>(null);
  readonly drawerOpen = signal(false);
  readonly projects = this.projectService.projects;

  // Queue sidebar signals
  readonly selectedQueueId = signal<string | null>(null);
  readonly selectedProjectId = computed(() => this.filters().project_id ?? null);

  // Filter signals
  readonly filters = signal<JobFilters>({});

  // DLQ signals
  readonly retryingAll = signal(false);
  readonly isDeadLetterFilterActive = computed(() => this.filters().status?.includes('dead_letter') ?? false);

  // System cleanup signal — true while the cleanupAllJobs request is
  // in-flight. Used to disable the System Cleanup button via
  // [disabled] in the template.
  readonly cleanupInProgress = signal(false);

  // Deleted jobs filter
  readonly showDeleted = signal(false);

  // View mode signal (Phase 4) — 'queues' (legacy) or 'all-work'
  // (unified list backed by /api/work). Persisted in localStorage so
  // the user's preferred view survives a page reload.
  readonly viewMode = signal<JobsViewMode>('queues');
  private viewModeRestored = false;

  // Unified work list (Phase 4) — only populated when viewMode is
  // 'all-work'. The mapping to Job[] lives in ``displayedJobs`` so
  // the rest of the template can stay type-agnostic.
  readonly works = signal<Work[]>([]);
  
  // SSE connection status
  readonly isConnected = this.jobSseService.isConnected;
  readonly retryAttempt = this.jobSseService.retryAttempt;
  readonly isRetrying = this.jobSseService.isRetrying;
  readonly isFailed = this.jobSseService.isFailed;
  readonly connectionState = this.jobSseService.connectionState;

  // Computed map of project_id -> job_queue_paused
  readonly projectPauseMap = computed(() => {
    const map = new Map<string, boolean>();
    for (const project of this.projects()) {
      map.set(project.project_id, project.job_queue_paused);
    }
    return map;
  });

  // Get pause state for the currently selected project
  readonly isCurrentProjectPaused = computed(() => {
    const projectId = this.selectedProjectId();
    return projectId ? (this.projectPauseMap().get(projectId) ?? false) : false;
  });

  // Get pause state for a specific project
  readonly getProjectPaused = (projectId: string): boolean => {
    return this.projectPauseMap().get(projectId) ?? false;
  };

  // Queue name map for job cards (queue_id -> queue_name)
  readonly queueNameMap = computed(() => {
    const map = new Map<string, string>();
    const queues = this.queueService.queues();
    for (const queue of queues) {
      map.set(queue.queue_id, queue.queue_name);
    }
    return map;
  });

  // Computed values
  readonly filteredJobs = computed(() => {
    const currentFilters = this.filters();
    const queueId = this.selectedQueueId();
    let filtered = this.jobs();

    if (currentFilters.status && currentFilters.status.length > 0) {
      filtered = filtered.filter(job => currentFilters.status!.includes(job.status));
    }
    if (currentFilters.source) {
      filtered = filtered.filter(job => job.source === currentFilters.source);
    }
    if (currentFilters.agent_id) {
      filtered = filtered.filter(job => job.agent_id === currentFilters.agent_id);
    }
    if (queueId) {
      filtered = filtered.filter(job => job.queue_id === queueId);
    }

    return filtered;
  });

  readonly hasJobs = computed(() => this.filteredJobs().length > 0);
  readonly isEmptyState = computed(() => !this.loading() && this.filteredJobs().length === 0 && !this.error());

  // Phase 4 — unified work view computeds.

  /**
   * Source for the displayed list — either the legacy filteredJobs
   * (queues view) or jobs synthesised from the WorkService response
   * (all-work view). The card template stays type-stable on ``Job``
   * so it does not need to branch on view mode.
   */
  readonly displayedJobs = computed<Job[]>(() => {
    if (this.viewMode() === 'all-work') {
      return this.worksAsJobs();
    }
    return this.filteredJobs();
  });

  /**
   * Map ``Work`` records onto the ``Job`` shape that ``JobCardComponent``
   * already knows how to render.
   *
   * The mapping is deliberately one-way and lossy — turn / report rows
   * do not have a ``message`` or ``priority`` in the backend, so the
   * JobCardComponent's ``messagePreview`` falls back to ``result_summary``
   * and the priority badge reads as ``P0``. The ``kind`` field is what
   * carries the semantic difference; that is the whole point of the
   * kind chip.
   *
   * The map also pins ``queue_id`` to ``null`` for non-job kinds so a
   * stale value cannot accidentally re-enable the queue badge after
   * the kind guardrail runs in JobCardComponent.
   */
  private worksAsJobs(): Job[] {
    return this.works().map((work) => this.workToJob(work));
  }

  /**
   * Single-row Work → Job mapper. Kept private and pure so it can be
   * reused by the SSE update path when a work_id event arrives.
   */
  private workToJob(work: Work): Job {
    return {
      job_id: work.work_id,
      agent_id: work.agent_id ?? '',
      message: undefined,
      source: undefined,
      project_id: work.project_id,
      priority: 0,
      status: (work.status as Job['status']) ?? 'pending',
      created_at: work.created_at,
      started_at: null,
      completed_at: null,
      instance_id: work.instance_id,
      error_message: work.error,
      result_summary: work.result_summary,
      queue_id: null,
      cancelled_at: null,
      kind: work.kind,
    };
  }

  /**
   * Empty-state flag for the unified work view (Phase 4).
   */
  readonly isEmptyWorkState = computed(() => {
    return this.viewMode() === 'all-work'
      && !this.workLoading()
      && this.works().length === 0
      && !this.workError();
  });

  /**
   * Convenience boolean — true while the page is in the all-work view.
   */
  readonly isAllWorkView = computed(() => this.viewMode() === 'all-work');

  /**
   * Convenience accessors for the WorkService signals so the template
   * does not need to reach into a private field. Wrapping in computed
   * is intentional — it lets Angular track the dependency cleanly
   * through the template change-detection cycle.
   */
  readonly workLoading = computed(() => this.workService.loading());
  readonly workError = computed(() => this.workService.error());

  // Status filter options
  readonly statusOptions: { value: JobStatus; label: string }[] = [
    { value: 'pending', label: 'Pending' },
    { value: 'processing', label: 'Processing' },
    { value: 'paused', label: 'Paused' },
    { value: 'completed', label: 'Completed' },
    { value: 'failed', label: 'Failed' },
    { value: 'cancelled', label: 'Cancelled' },
    { value: 'dead_letter', label: 'Dead Letter' }
  ];

  // Source filter options
  readonly sourceOptions: { value: JobSource | 'all'; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'api', label: 'API' },
    { value: 'telegram', label: 'Telegram' },
    { value: 'scheduler', label: 'Scheduler' },
    { value: 'webhook', label: 'Webhook' }
  ];

  // Project filter options — derive from ProjectService.projects() and
  // lead with a sentinel empty-string option so the user can deselect
  // the project. Matches the shape SearchableSelectComponent expects
  // ({value, label}).
  readonly projectOptions = computed(() => [
    { value: '', label: 'Select project' },
    ...this.projects().map((p) => ({ value: p.project_id, label: p.name })),
  ]);

  // Agent filter options — derive from agents() and lead with an
  // "all" sentinel so the filter can clear. Label uses the existing
  // getAgentDisplayName helper so the dropdown stays consistent with
  // the legacy mat-option rendering (icon + name).
  readonly agentOptions = computed(() => [
    { value: 'all', label: 'All Agents' },
    ...this.agents().map((a) => ({ value: a.id, label: this.getAgentDisplayName(a.id) })),
  ]);

  constructor() {
    // Effect to handle job status updates from SSE
    effect(() => {
      const latestStatus = this.jobSseService.latestStatus();
      if (latestStatus && latestStatus.job_id) {
        this.updateJobFromSse(latestStatus);
      }
    });

    // Effect to handle SSE errors with user-friendly messages
    effect(() => {
      const latestError = this.jobSseService.latestError();
      const state = this.jobSseService.connectionState();
      const attempt = this.retryAttempt();

      if (latestError) {
        console.error('[Jobs] SSE error:', latestError);
        // Show user-friendly error message based on state
        let displayMessage = latestError;

        if (state === 'retrying') {
          displayMessage = `Connection lost. Reconnecting... (attempt ${attempt})`;
        } else if (state === 'failed') {
          displayMessage = latestError;
        }

        this.snackBar.open(displayMessage, 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });

        // Clear the error after showing it to prevent duplicate notifications
        this.jobSseService.clearError();
      }
    });

    // Effect to restore last selected project from localStorage
    effect(() => {
      const projectList = this.projects();
      if (projectList.length > 0) {
        this.tryRestoreProject();
      }
    });

    // Effect to restore the persisted view mode ('queues' vs.
    // 'all-work') once on first read. Uses a guard flag so it does
    // not race with subsequent user-driven view-mode changes.
    effect(() => {
      if (this.viewModeRestored) {
        return;
      }
      this.viewModeRestored = true;
      this.tryRestoreViewMode();
    });
  }

  ngOnInit(): void {
    this.loadJobs();
    this.loadAgents();
    this.loadProjects();
    this.startAutoRefresh();
    // Phase 4 — kick off an initial WorkService fetch in parallel so
    // switching to the All Work view later is instantaneous. The fetch
    // is harmless if the user never toggles the view mode.
    this.loadWorks();
  }

  private tryRestoreProject(): void {
    if (this.projectRestored) {
      return;
    }
    this.projectRestored = true;

    let savedProjectId: string | null = null;
    try {
      savedProjectId = localStorage.getItem(this.STORAGE_KEY);
    } catch {
      // silently ignore
    }
    if (!savedProjectId) {
      return;
    }

    // Check if saved project still exists in the project list
    const projectExists = this.projects().some(p => p.project_id === savedProjectId);
    if (projectExists) {
      // Directly set the filter without calling loadJobs() — ngOnInit already called it
      this.filters.update(f => ({ ...f, project_id: savedProjectId }));
    } else {
      // Clear stale entry
      try {
        localStorage.removeItem(this.STORAGE_KEY);
      } catch {
        // silently ignore
      }
    }
  }

  /**
   * Restore the persisted view mode ('queues' vs 'all-work') from
   * localStorage. Wrapped in try/catch for private-browsing safety
   * — matches the pattern used by ``tryRestoreProject``.
   */
  private tryRestoreViewMode(): void {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(this.VIEW_MODE_KEY);
    } catch {
      return;
    }
    if (saved === 'queues' || saved === 'all-work') {
      this.viewMode.set(saved);
      // If the user previously left the page in all-work view, kick
      // off an initial fetch so the list is not blank on reload.
      if (saved === 'all-work') {
        this.loadWorks();
      }
    }
  }

  ngOnDestroy(): void {
    this.stopAutoRefresh();
    this.jobSseService.disconnect();
    this.jobSseService.clearEvents();
    if (this.sseSubscription) {
      this.sseSubscription.unsubscribe();
    }
  }

  private loadProjects(): void {
    this.projectService.listProjects().subscribe({
      next: () => {},
      error: (err) => {
        console.error('Failed to load projects:', err);
      }
    });
  }

  private loadJobs(): void {
    this.loading.set(true);
    this.error.set(null);

    this.jobService.listJobs(this.filters()).subscribe({
      next: (jobs) => {
        this.jobs.set(jobs);
        this.loading.set(false);
      },
      error: (err) => {
        console.error('Failed to load jobs:', err);
        this.error.set(err.message || 'Failed to load jobs');
        this.loading.set(false);
      }
    });
  }

  /**
   * Phase 4 — fetch the unified work list from ``WorkService``.
   *
   * Filters mirror what the page already exposes for the legacy
   * Jobs view (status / project_id); the queue sidebar stays inactive
   * in this view so we deliberately do NOT push ``queue_id`` into the
   * filter payload — that filter would force the backend to return
   * ONLY queued work, defeating the unified surface.
   *
   * ``root_only`` is hard-coded to ``false`` here (P-A of the Virtual
   * Job Tool Completeness plan). The "All Work" view is contractually
   * named — it must show every row the resolver can find, including
   * child-instance turns and reports. The backend default is
   * ``root_only=true`` (jober-management view, excludes children);
   * we override that default at the only call site that represents
   * "all" to the user. The Queues view goes through ``JobService``
   * and is unaffected.
   *
   * Errors are non-fatal — the legacy Jobs list still renders and the
   * snackbar gives the operator a hint about why the work list is
   * empty.
   */
  private loadWorks(): void {
    const projectId = this.filters().project_id;
    const statusFilter = this.filters().status;
    this.workService.getWork({
      project_id: projectId || undefined,
      status: statusFilter && statusFilter.length > 0 ? statusFilter.join(',') : undefined,
      // P-A — the All Work view intentionally bypasses the root-only
      // filter so child-instance rows stay visible. See the method
      // docstring for the rationale.
      root_only: false,
    }).subscribe({
      next: (works) => {
        this.works.set(works);
      },
      error: (err) => {
        console.error('[Jobs] Failed to load works:', err);
        // Surface the error to the user; do NOT clear the legacy
        // Jobs signal — operators may still want to use that view.
        this.snackBar.open(
          err?.message || 'Failed to load unified work list',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  private loadAgents(): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
      }
    });
  }

  private startAutoRefresh(): void {
    this.refreshInterval = setInterval(() => {
      if (this.viewMode() === 'all-work') {
        // Refresh whichever view is currently active. The legacy
        // refresh path stays untouched so other call sites are not
        // disturbed.
        if (!this.workService.loading()) {
          this.loadWorks();
        }
        return;
      }
      if (!this.loading()) {
        this.jobService.refreshJobs(this.filters());
      }
    }, 30000); // 30 seconds
  }

  private stopAutoRefresh(): void {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
      this.refreshInterval = null;
    }
  }

  private updateJobFromSse(status: JobEventPayload): void {
    this.jobs.update(jobs =>
      jobs.map(job =>
        job.job_id === status.job_id
          ? {
              ...job,
              status: status.status || job.status,
              queue_id: status.queue_id ?? job.queue_id,
              instance_id: status.instance_id || job.instance_id,
              result_summary: status.result_summary || job.result_summary,
              error_message: status.error_message || job.error_message,
              completed_at: status.status === 'completed' || status.status === 'failed'
                ? new Date().toISOString()
                : job.completed_at,
              started_at: status.status === 'processing' && !job.started_at
                ? new Date().toISOString()
                : job.started_at
            }
          : job
      )
    );

    // Phase 4 — also patch the unified Work list so SSE updates
    // land on the right record in the All Work view too. The SSE
    // payload uses ``job_id`` as the work_id key — the backend SSE
    // endpoint already resolves work_id through WorkResolverService,
    // so the same status update is valid for both Job and Work rows.
    this.works.update(works =>
      works.map(work =>
        work.work_id === status.job_id
          ? {
              ...work,
              status: status.status || work.status,
              instance_id: status.instance_id ?? work.instance_id,
              result_summary: status.result_summary ?? work.result_summary,
              error: status.error_message ?? work.error,
            }
          : work
      )
    );
  }

  protected onRefresh(): void {
    if (this.viewMode() === 'all-work') {
      this.loadWorks();
    } else {
      this.loadJobs();
    }
  }

  /**
   * Phase 4 — switch between 'queues' (legacy) and 'all-work'
   * (unified list backed by /api/work). Persists the choice so it
   * survives a reload.
   *
   * Switching INTO 'all-work' triggers an immediate fetch if the
   * work list is empty — the initial ngOnInit fetch may have raced
   * with the first paint and we do not want the user to see a
   * stale blank list.
   */
  protected onViewModeChange(mode: JobsViewMode): void {
    if (this.viewMode() === mode) {
      return;
    }
    this.viewMode.set(mode);
    try {
      localStorage.setItem(this.VIEW_MODE_KEY, mode);
    } catch {
      // Private-browsing — silently ignore.
    }
    if (mode === 'all-work' && this.works().length === 0) {
      this.loadWorks();
    }
  }

  protected onStatusFilterChange(statuses: JobStatus[]): void {
    this.filters.update(filters => ({
      ...filters,
      status: statuses.length > 0 ? statuses : undefined
    }));
    this.loadJobs();
  }

  protected onSourceFilterChange(source: JobSource | 'all'): void {
    this.filters.update(filters => ({
      ...filters,
      source: source === 'all' ? undefined : source
    }));
    this.loadJobs();
  }

  protected onAgentFilterChange(agentId: string): void {
    this.filters.update(filters => ({
      ...filters,
      agent_id: agentId === 'all' ? undefined : agentId
    }));
    this.loadJobs();
  }

  protected onProjectFilterChange(projectId: string): void {
    this.filters.update(filters => ({
      ...filters,
      project_id: projectId || undefined
    }));
    // Clear queue selection when project changes
    this.selectedQueueId.set(null);
    // Persist selection to localStorage
    try {
      if (projectId) {
        localStorage.setItem(this.STORAGE_KEY, projectId);
      } else {
        localStorage.removeItem(this.STORAGE_KEY);
      }
    } catch {
      // silently ignore
    }
    this.loadJobs();
  }

  protected onClearFilters(): void {
    this.filters.set({});
    this.selectedQueueId.set(null);
    this.showDeleted.set(false);
    // Clear localStorage so the project isn't silently restored on next visit
    try {
      localStorage.removeItem(this.STORAGE_KEY);
    } catch {
      // silently ignore
    }
    this.loadJobs();
  }

  protected onToggleShowDeleted(checked: boolean): void {
    this.showDeleted.set(checked);
    this.filters.update(filters => ({
      ...filters,
      include_deleted: checked ? true : undefined
    }));
    this.loadJobs();
  }

  protected onQueueSelected(queueId: string | null): void {
    this.selectedQueueId.set(queueId);
    this.filters.update(filters => ({
      ...filters,
      queue_id: queueId || undefined
    }));
    this.loadJobs();
  }

  protected onQueueChanged(): void {
    this.loadJobs();
  }

  protected onOpenCreateDialog(): void {
    const dialogRef = this.dialog.open(JobCreateDialogComponent, {
      width: '500px',
      panelClass: 'dark-modal-panel',
      data: {
        agentId: 'leader',
        projectId: this.selectedProjectId() || undefined
      }
    });

    dialogRef.afterClosed().subscribe((result: JobCreateDialogResult | undefined) => {
      if (result) {
        this.createJob(result);
      }
    });
  }

  private createJob(data: JobCreateDialogResult): void {
    this.jobService.createJob({
      agent_id: data.agent_id,
      message: data.message,
      project_id: data.project_id,
      priority: data.priority,
      source: data.source as JobSource,
      queue_id: data.queue_id
    }).subscribe({
      next: (job) => {
        this.snackBar.open('Job created successfully', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.loadJobs();
      },
      error: (err) => {
        console.error('Failed to create job:', err);
        this.snackBar.open(
          err.message || 'Failed to create job',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onCancelJob(job: Job): void {
    this.jobService.cancelJob(job.job_id).subscribe({
      next: () => {
        this.snackBar.open('Job cancelled', 'Close', {
          duration: 3000
        });
      },
      error: (err) => {
        console.error('Failed to cancel job:', err);
        this.snackBar.open(
          err.message || 'Failed to cancel job',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onRetryJob(job: Job): void {
    this.jobService.retryJob(job.job_id).subscribe({
      next: () => {
        this.snackBar.open('Job retry scheduled', 'Close', {
          duration: 3000
        });
      },
      error: (err) => {
        console.error('Failed to retry job:', err);
        this.snackBar.open(
          err.message || 'Failed to retry job',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onDeleteJob(job: Job): void {
    this.jobService.softDeleteJob(job.job_id).subscribe({
      next: () => {
        this.snackBar.open('Job deleted', 'Undo', { duration: 5000 })
          .onAction().subscribe(() => {
            this.jobService.restoreJob(job.job_id).subscribe({
              next: () => this.loadJobs(),
              error: () => {}
            });
          });
        if (!this.showDeleted()) {
          // Remove from local list
          this.jobs.update(jobs => jobs.filter(j => j.job_id !== job.job_id));
        } else {
          // Update the job in place (show as deleted)
          this.jobs.update(jobs =>
            jobs.map(j => j.job_id === job.job_id ? { ...j, deleted_at: new Date().toISOString() } : j)
          );
        }
      },
      error: (err) => {
        this.snackBar.open(err.message || 'Failed to delete job', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onRestoreJob(job: Job): void {
    this.jobService.restoreJob(job.job_id).subscribe({
      next: () => this.loadJobs(),
      error: (err) => {
        this.snackBar.open(err.message || 'Failed to restore job', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onRetryAllDeadLetterJobs(): void {
    const projectId = this.filters().project_id;
    if (!projectId) {
      this.snackBar.open('Please select a project first', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar'
      });
      return;
    }

    this.retryingAll.set(true);
    this.jobService.retryAllDeadLetterJobs(projectId).subscribe({
      next: (result) => {
        this.retryingAll.set(false);
        this.snackBar.open(
          `Replayed ${result.replayed} job${result.replayed !== 1 ? 's' : ''}${result.failed > 0 ? `, ${result.failed} failed` : ''}`,
          'Close',
          { duration: 5000 }
        );
        this.loadJobs();
      },
      error: (err) => {
        console.error('Failed to retry all dead letter jobs:', err);
        this.retryingAll.set(false);
        this.snackBar.open(
          err.message || 'Failed to retry all dead letter jobs',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  /**
   * Open the System Cleanup confirmation dialog and (on confirm) call
   * ``POST /api/jobs/cleanup`` to cancel every queued and active job
   * across all projects.
   *
   * Guards:
   *   * Cleanup already in progress — silently no-op so a double-click
   *     cannot fire two parallel backend requests.
   *
   * The backend endpoint is intentionally global (it cancels across
   * all projects — this is a "system reset" operation), so no project
   * filter is required here. The confirmation dialog makes the global
   * scope explicit.
   *
   * The backend now also reaps *orphan* active jobs — rows whose
   * underlying instance is already terminal but whose
   * ``admission_state='active'`` was leaked (e.g. observer feedback
   * dropped because the worker process died mid-ack). They surface
   * as ``orphaned_reaped`` in the response and are surfaced in the
   * success snackbar so the operator can see the ghost rows were
   * drained without a second round-trip.
   *
   * On success a success snackbar reports the cancelled counts and the
   * active view is refreshed; on error an error snackbar surfaces the
   * failure message. The ``cleanupInProgress`` signal is always reset
   * before the method returns so the button re-enables.
   */
  protected onSystemCleanup(): void {
    if (this.cleanupInProgress()) {
      return;
    }

    const dialogRef = this.dialog.open(SystemCleanupConfirmDialogComponent, {
      width: '420px',
      panelClass: 'dark-modal-panel',
    });

    dialogRef.afterClosed().subscribe((confirmed: boolean | undefined) => {
      if (!confirmed) {
        return;
      }
      this.cleanupInProgress.set(true);
      this.jobService.cleanupAllJobs().subscribe({
        next: (result) => {
          this.cleanupInProgress.set(false);
          const orphaned = result.orphaned_reaped ?? 0;
          const orphanSuffix = orphaned > 0 ? `, reaped ${orphaned} orphan active` : '';
          this.snackBar.open(
            `Cancelled ${result.cancelled_queued} queued, ${result.cancelled_active} active${orphanSuffix} jobs`,
            'Close',
            { duration: 3000, panelClass: 'success-snackbar' }
          );
          this.onRefresh();
        },
        error: (err) => {
          console.error('Failed to cleanup jobs:', err);
          this.cleanupInProgress.set(false);
          this.snackBar.open(
            err?.message || 'Failed to cleanup jobs',
            'Dismiss',
            {
              duration: 5000,
              panelClass: 'error-snackbar'
            }
          );
        },
      });
    });
  }

  protected onViewJobDetails(job: Job): void {
    this.selectedJob.set(job);
    this.drawerOpen.set(true);

    // Don't connect to SSE for terminal jobs - no live updates needed
    if (isTerminalStatus(job.status)) {
      return;
    }

    // Connect to SSE for real-time updates on this job
    this.jobSseService.disconnect();
    this.jobSseService.clearEvents();
    this.sseSubscription = this.jobSseService.streamJobEvents(job.job_id).subscribe();
  }

  protected onCloseDrawer(): void {
    this.drawerOpen.set(false);
    this.selectedJob.set(null);
    this.jobSseService.disconnect();
    if (this.sseSubscription) {
      this.sseSubscription.unsubscribe();
      this.sseSubscription = null;
    }
  }

  protected onDrawerCancelJob(jobId: string): void {
    const job = this.jobs().find(j => j.job_id === jobId);
    if (job) {
      this.onCancelJob(job);
    }
  }

  protected onDrawerRetryJob(jobId: string): void {
    const job = this.jobs().find(j => j.job_id === jobId);
    if (job) {
      this.onRetryJob(job);
    }
  }

  protected onDrawerViewInstance(instanceId: string): void {
    const projectContext = this.tabStateService.activeProjectId() ?? 'all';
    this.router.navigate(['/projects', projectContext, 'instances', instanceId]);
  }

  protected getAgentDisplayName(agentId: string): string {
    const agent = this.agents().find(a => a.id === agentId);
    return agent ? `${agent.icon} ${agent.name}` : agentId;
  }

  protected hasActiveFilters(): boolean {
    const filters = this.filters();
    return !!(filters.status || filters.source || filters.agent_id || filters.queue_id);
  }

  protected isProjectSelected(): boolean {
    return !!this.filters().project_id;
  }

  // Handle project pause change from queue-list header
  protected onProjectPauseChanged(isPaused: boolean): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;

    // Update local project state
    this.projectService.projects.update(projects => 
      projects.map(p => 
        p.project_id === projectId 
          ? { ...p, job_queue_paused: isPaused }
          : p
      )
    );
  }
}
