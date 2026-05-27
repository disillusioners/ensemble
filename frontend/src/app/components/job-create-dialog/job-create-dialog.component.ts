import { Component, inject, signal, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { ProjectService } from '../../services/project.service';
import { QueueService } from '../../services/queue.service';
import type { Agent } from '../../models';
import type { Project } from '../../models/project.model';
import { JobQueue, getQueueTypeLabel } from '../../models/job-queue.model';

export interface JobCreateDialogData {
  editMode?: boolean;
  jobId?: string;
  agentId?: string;
  message?: string;
  projectId?: string;
  priority?: number;
  source?: string;
}

export interface JobCreateDialogResult {
  agent_id: string;
  message: string;
  project_id?: string;
  priority: number;
  source: string;
  queue_id?: string;
}

@Component({
  selector: 'app-job-create-dialog',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatSnackBarModule
  ],
  templateUrl: './job-create-dialog.html',
  styleUrl: './job-create-dialog.scss'
})
export class JobCreateDialogComponent implements OnInit, OnDestroy {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(ApiService);
  protected readonly projectService = inject(ProjectService);
  protected readonly queueService = inject(QueueService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroy$ = new Subject<void>();
  
  protected readonly dialogRef = inject(MatDialogRef<JobCreateDialogComponent>);
  protected readonly data = inject<JobCreateDialogData>(MAT_DIALOG_DATA);

  protected readonly agents = signal<Agent[]>([]);
  protected readonly isLoading = signal(false);
  protected readonly agentsLoading = signal(true);
  protected readonly queues = signal<JobQueue[]>([]);
  protected readonly queuesLoading = signal(false);

  protected readonly form: FormGroup = this.fb.group({
    agent_id: ['', Validators.required],
    message: ['', [Validators.required, Validators.minLength(10)]],
    project_id: [''],
    queue_id: [''],
    priority: [5, [Validators.required, Validators.min(1), Validators.max(10)]],
    source: ['api']
  });

  protected readonly sourceOptions = [
    { value: 'api', label: 'API' },
    { value: 'telegram', label: 'Telegram' },
    { value: 'scheduler', label: 'Scheduler' },
    { value: 'webhook', label: 'Webhook' }
  ];

  ngOnInit(): void {
    this.loadAgents();
    this.loadProjects();
    
    // Subscribe to project_id changes to load queues
    this.form.get('project_id')?.valueChanges
      .pipe(takeUntil(this.destroy$))
      .subscribe((projectId: string | null) => {
        if (projectId) {
          this.loadQueues(projectId);
        } else {
          this.queues.set([]);
        }
      });
    
    // Pre-fill form if editing, or if projectId/agentId is provided
    if (this.data?.editMode || this.data?.projectId || this.data?.agentId) {
      this.form.patchValue({
        agent_id: this.data.agentId || '',
        message: this.data.message || '',
        project_id: this.data.projectId || '',
        priority: this.data.priority || 5,
        source: this.data.source || 'api'
      });
      // Load queues for the pre-selected project
      if (this.data.projectId) {
        this.loadQueues(this.data.projectId);
      }
    }
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  private loadQueues(projectId: string): void {
    this.queuesLoading.set(true);

    this.queueService.listQueues(projectId).subscribe({
      next: (queues) => {
        this.queues.set(queues);
        // Default to system_defer_queue if available
        const defaultQueue = queues.find(q => q.queue_name === 'system_defer_queue');
        if (defaultQueue) {
          this.form.get('queue_id')?.setValue(defaultQueue.queue_id);
        }
        this.queuesLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load queues:', err);
        this.queuesLoading.set(false);
      }
    });
  }

  private loadAgents(): void {
    this.agentsLoading.set(true);
    
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
        this.agentsLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
        this.snackBar.open('Failed to load agents', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
        this.agentsLoading.set(false);
      }
    });
  }

  private loadProjects(): void {
    this.projectService.listProjects().subscribe({
      error: (err) => {
        console.error('Failed to load projects:', err);
      }
    });
  }

  protected handleClose(): void {
    this.dialogRef.close();
  }

  protected handleSubmit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.isLoading.set(true);

    try {
      const result: JobCreateDialogResult = {
        agent_id: this.form.value.agent_id,
        message: this.form.value.message,
        project_id: this.form.value.project_id || undefined,
        priority: this.form.value.priority,
        source: this.form.value.source,
        queue_id: this.form.value.queue_id || undefined
      };

      this.dialogRef.close(result);
    } catch (err) {
      console.error('Failed to create job:', err);
      this.snackBar.open(
        err instanceof Error ? err.message : 'Failed to create job',
        'Close',
        {
          duration: 5000,
          panelClass: 'error-snackbar'
        }
      );
    } finally {
      this.isLoading.set(false);
    }
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || this.form.invalid;
  }

  protected getQueueTypeLabel = getQueueTypeLabel;

  protected getAgentDisplayName(agent: Agent): string {
    return `${agent.icon} ${agent.name}`;
  }
}
