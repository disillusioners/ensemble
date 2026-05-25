import { Component, signal, computed, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { Subscription, switchMap, of, catchError, tap } from 'rxjs';
import { JobService } from '../../services/job.service';
import { JobSseService } from '../../services/job-sse.service';
import { ProjectService } from '../../services/project.service';
import { QueueService } from '../../services/queue.service';
import { ApiService } from '../../services/api.service';
import { JobCardComponent } from '../../components/job-card/job-card.component';
import { JobDetailDrawerComponent } from '../../components/job-detail-drawer/job-detail-drawer.component';
import { JobCreateDialogComponent, JobCreateDialogResult } from '../../components/job-create-dialog/job-create-dialog.component';
import { QueueListComponent } from '../../components/queue-list/queue-list.component';
import { Job, JobFilters, JobStatus, JobSource, JobEventPayload, isTerminalStatus } from '../../models/job.model';
import { JobQueue } from '../../models/job-queue.model';
import { Project } from '../../models/project.model';
import { Agent } from '../../models';

@Component({
  selector: 'app-jobs',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatSelectModule,
    MatFormFieldModule,
    MatSidenavModule,
    MatSnackBarModule,
    MatDialogModule,
    MatTooltipModule,
    MatCheckboxModule,
    JobCardComponent,
    JobDetailDrawerComponent,
    QueueListComponent
  ],
  templateUrl: './jobs.component.html',
  styleUrl: './jobs.component.scss'
})
export class JobsComponent implements OnInit, OnDestroy {
  private readonly router = inject(Router);
  private readonly jobService = inject(JobService);
  private readonly jobSseService = inject(JobSseService);
  private readonly projectService = inject(ProjectService);
  private readonly queueService = inject(QueueService);
  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  
  private readonly STORAGE_KEY = 'job-page-selected-project';
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
  
  // Deleted jobs filter
  readonly showDeleted = signal(false);
  
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

  // Status filter options
  readonly statusOptions: { value: JobStatus; label: string }[] = [
    { value: 'pending', label: 'Pending' },
    { value: 'processing', label: 'Processing' },
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
  }

  ngOnInit(): void {
    this.loadJobs();
    this.loadAgents();
    this.loadProjects();
    this.startAutoRefresh();
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
  }

  protected onRefresh(): void {
    this.loadJobs();
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
    this.router.navigate(['/instances', instanceId]);
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
