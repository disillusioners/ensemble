import { Component, signal, computed, inject, OnInit, OnDestroy, effect } from '@angular/core';
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
import { Subscription } from 'rxjs';
import { JobService } from '../../services/job.service';
import { JobSseService } from '../../services/job-sse.service';
import { ApiService } from '../../services/api.service';
import { JobCardComponent } from '../../components/job-card/job-card.component';
import { JobDetailDrawerComponent } from '../../components/job-detail-drawer/job-detail-drawer.component';
import { JobCreateDialogComponent, JobCreateDialogResult } from '../../components/job-create-dialog/job-create-dialog.component';
import { Job, JobFilters, JobStatus, JobSource, JobEventPayload } from '../../models/job.model';
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
    JobCardComponent,
    JobDetailDrawerComponent
  ],
  templateUrl: './jobs.component.html',
  styleUrl: './jobs.component.scss'
})
export class JobsComponent implements OnInit, OnDestroy {
  private readonly jobService = inject(JobService);
  private readonly jobSseService = inject(JobSseService);
  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  
  private refreshInterval: ReturnType<typeof setInterval> | null = null;
  private sseSubscription: Subscription | null = null;

  // Signals for state
  readonly jobs = signal<Job[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly agents = signal<Agent[]>([]);
  readonly selectedJob = signal<Job | null>(null);
  readonly drawerOpen = signal(false);
  
  // Filter signals
  readonly filters = signal<JobFilters>({});
  
  // SSE connection status
  readonly isConnected = this.jobSseService.isConnected;

  // Computed values
  readonly filteredJobs = computed(() => {
    const currentFilters = this.filters();
    let filtered = this.jobs();

    if (currentFilters.status) {
      filtered = filtered.filter(job => job.status === currentFilters.status);
    }
    if (currentFilters.source) {
      filtered = filtered.filter(job => job.source === currentFilters.source);
    }
    if (currentFilters.agent_dir) {
      filtered = filtered.filter(job => job.agent_dir === currentFilters.agent_dir);
    }

    return filtered;
  });

  readonly hasJobs = computed(() => this.filteredJobs().length > 0);
  readonly isEmptyState = computed(() => !this.loading() && this.filteredJobs().length === 0 && !this.error());

  // Status filter options
  readonly statusOptions: { value: JobStatus | 'all'; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'pending', label: 'Pending' },
    { value: 'processing', label: 'Processing' },
    { value: 'completed', label: 'Completed' },
    { value: 'failed', label: 'Failed' },
    { value: 'cancelled', label: 'Cancelled' }
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

    // Effect to handle SSE errors
    effect(() => {
      const latestError = this.jobSseService.latestError();
      if (latestError) {
        console.error('[Jobs] SSE error:', latestError);
        this.snackBar.open(`Connection error: ${latestError}`, 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  ngOnInit(): void {
    this.loadJobs();
    this.loadAgents();
    this.startAutoRefresh();
  }

  ngOnDestroy(): void {
    this.stopAutoRefresh();
    this.jobSseService.disconnect();
    this.jobSseService.clearEvents();
    if (this.sseSubscription) {
      this.sseSubscription.unsubscribe();
    }
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
              session_id: status.session_id || job.session_id,
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
    this.jobService.refreshJobs(this.filters());
  }

  protected onStatusFilterChange(status: JobStatus | 'all'): void {
    this.filters.update(filters => ({
      ...filters,
      status: status === 'all' ? undefined : status
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

  protected onAgentFilterChange(agentDir: string): void {
    this.filters.update(filters => ({
      ...filters,
      agent_dir: agentDir === 'all' ? undefined : agentDir
    }));
    this.loadJobs();
  }

  protected onClearFilters(): void {
    this.filters.set({});
    this.loadJobs();
  }

  protected onOpenCreateDialog(): void {
    const dialogRef = this.dialog.open(JobCreateDialogComponent, {
      width: '500px',
      panelClass: 'dark-modal-panel',
      data: {}
    });

    dialogRef.afterClosed().subscribe((result: JobCreateDialogResult | undefined) => {
      if (result) {
        this.createJob(result);
      }
    });
  }

  private createJob(data: JobCreateDialogResult): void {
    this.jobService.createJob({
      agent_dir: data.agent_dir,
      message: data.message,
      project_id: data.project_id,
      priority: data.priority,
      source: data.source as JobSource
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

  protected onViewJobDetails(job: Job): void {
    this.selectedJob.set(job);
    this.drawerOpen.set(true);

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

  protected onDrawerViewSession(sessionId: string): void {
    // Navigate to session page
    // This would typically use Router, but we're keeping it simple
    console.log('[Jobs] View session:', sessionId);
    this.snackBar.open(`Session ID: ${sessionId}`, 'Close', {
      duration: 3000
    });
  }

  protected getAgentDisplayName(agentDir: string): string {
    const agent = this.agents().find(a => a.agent_dir === agentDir);
    return agent ? `${agent.icon} ${agent.name}` : agentDir;
  }

  protected hasActiveFilters(): boolean {
    const filters = this.filters();
    return !!(filters.status || filters.source || filters.agent_dir);
  }
}
