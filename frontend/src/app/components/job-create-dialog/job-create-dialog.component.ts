import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSliderModule } from '@angular/material/slider';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Inject } from '@angular/core';
import { ApiService } from '../../services/api.service';
import type { Agent } from '../../models';

export interface JobCreateDialogData {
  editMode?: boolean;
  jobId?: string;
  agentDir?: string;
  message?: string;
  projectId?: string;
  priority?: number;
  source?: string;
}

export interface JobCreateDialogResult {
  agent_dir: string;
  message: string;
  project_id?: string;
  priority: number;
  source: string;
}

@Component({
  selector: 'app-job-create-dialog',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatSliderModule,
    MatSnackBarModule
  ],
  templateUrl: './job-create-dialog.html',
  styleUrl: './job-create-dialog.scss'
})
export class JobCreateDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(ApiService);
  private readonly snackBar = inject(MatSnackBar);
  
  protected readonly dialogRef = inject(MatDialogRef<JobCreateDialogComponent>);
  protected readonly data = inject<JobCreateDialogData>(MAT_DIALOG_DATA);

  protected readonly agents = signal<Agent[]>([]);
  protected readonly isLoading = signal(false);
  protected readonly agentsLoading = signal(true);

  protected readonly form: FormGroup = this.fb.group({
    agent_dir: ['', Validators.required],
    message: ['', [Validators.required, Validators.minLength(10)]],
    project_id: [''],
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
    
    // Pre-fill form if editing
    if (this.data?.editMode && this.data.agentDir) {
      this.form.patchValue({
        agent_dir: this.data.agentDir || '',
        message: this.data.message || '',
        project_id: this.data.projectId || '',
        priority: this.data.priority || 5,
        source: this.data.source || 'api'
      });
    }
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

  protected handleClose(): void {
    this.dialogRef.close();
  }

  protected async handleSubmit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.isLoading.set(true);

    try {
      const result: JobCreateDialogResult = {
        agent_dir: this.form.value.agent_dir,
        message: this.form.value.message,
        project_id: this.form.value.project_id || undefined,
        priority: this.form.value.priority,
        source: this.form.value.source
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

  protected getAgentDisplayName(agent: Agent): string {
    return `${agent.icon} ${agent.name}`;
  }
}
