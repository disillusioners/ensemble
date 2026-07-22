import { Component, inject, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators, AbstractControl, ValidationErrors } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { ApiService } from '../../services/api.service';
import { SchedulerService, ValidationResponse } from '../../services/scheduler.service';
import { Agent } from '../../models';
import { SearchableSelectComponent } from '../../components';

// Common timezones
const TIMEZONES = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'Eastern Time (US)' },
  { value: 'America/Chicago', label: 'Central Time (US)' },
  { value: 'America/Denver', label: 'Mountain Time (US)' },
  { value: 'America/Los_Angeles', label: 'Pacific Time (US)' },
  { value: 'Europe/London', label: 'London' },
  { value: 'Europe/Paris', label: 'Paris' },
  { value: 'Europe/Berlin', label: 'Berlin' },
  { value: 'Asia/Tokyo', label: 'Tokyo' },
  { value: 'Asia/Shanghai', label: 'Shanghai' },
  { value: 'Asia/Singapore', label: 'Singapore' },
  { value: 'Australia/Sydney', label: 'Sydney' },
];

// Standalone validator function (defined before class to avoid initialization order issues)
function scheduleValidator(control: AbstractControl): ValidationErrors | null {
  const type = control.get('type')?.value;
  const schedule = control.get('schedule')?.value;
  const intervalSeconds = control.get('interval_seconds')?.value;
  const runAt = control.get('run_at')?.value;
  
  if (type === 'cron' && !schedule) {
    return { scheduleRequired: true };
  }
  if (type === 'interval' && (!intervalSeconds || intervalSeconds < 1)) {
    return { intervalRequired: true };
  }
  if (type === 'one-time' && !runAt) {
    return { runAtRequired: true };
  }
  
  return null;
}

export interface ScheduleCreateDialogData {
  editMode?: boolean;
  scheduleId?: string;
  name?: string;
  agent?: string;
  message?: string;
  project?: string;
  timezone?: string;
  session_mode?: 'new_session' | 'reuse_session';
}

export interface ScheduleCreateDialogResult {
  name: string;
  agent: string;
  message: string;
  project?: string;
  timezone: string;
  schedule_type: 'cron' | 'interval' | 'one-time';
  session_mode: 'new_session' | 'reuse_session';
  schedule?: string;
  interval_seconds?: number;
  run_at?: string;
}

@Component({
  selector: 'app-schedule-create-dialog',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatDialogModule,
    MatSnackBarModule,
    SearchableSelectComponent
  ],
  templateUrl: './schedule-create-dialog.html',
  styleUrl: './schedule-create-dialog.scss'
})
export class ScheduleCreateDialogComponent implements OnInit {
  private readonly fb = inject(FormBuilder);
  private readonly api = inject(ApiService);
  private readonly schedulerService = inject(SchedulerService);
  private readonly snackBar = inject(MatSnackBar);
  
  protected readonly dialogRef = inject(MatDialogRef<ScheduleCreateDialogComponent>);
  protected readonly data = inject<ScheduleCreateDialogData>(MAT_DIALOG_DATA);

  protected readonly agents = signal<Agent[]>([]);
  protected readonly isLoading = signal(false);
  protected readonly agentsLoading = signal(true);
  protected readonly isValidating = signal(false);
  protected readonly isValid = signal<boolean | null>(null);
  protected readonly validationError = signal<string | null>(null);

  protected readonly timezones = TIMEZONES;
  protected readonly scheduleTypes = [
    { value: 'cron', label: 'Cron Expression' },
    { value: 'interval', label: 'Interval (seconds)' },
    { value: 'one-time', label: 'One-time' }
  ];

  protected readonly agentOptions = computed(() =>
    this.agents().map((agent) => ({
      value: agent.agent_id,
      label: this.getAgentDisplayName(agent),
    }))
  );

  protected readonly form: FormGroup = this.fb.group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    type: ['cron', Validators.required],
    session_mode: ['new_session'],
    agent: ['', Validators.required],
    message: ['', [Validators.required, Validators.minLength(5)]],
    project: [''],
    timezone: ['UTC'],
    // Cron
    schedule: [''],
    // Interval
    interval_seconds: [60, [Validators.min(1)]],
    // One-time
    run_at: ['']
  }, {
    validators: scheduleValidator
  });

  ngOnInit(): void {
    this.loadAgents();
    this.setupTypeChangeListener();
    
    // Pre-fill form if editing
    if (this.data?.editMode) {
      this.form.patchValue({
        name: this.data.name || '',
        agent: this.data.agent || '',
        message: this.data.message || '',
        project: this.data.project || '',
        timezone: this.data.timezone || 'UTC',
        session_mode: this.data.session_mode || 'new_session'
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

  private setupTypeChangeListener(): void {
    this.form.get('type')?.valueChanges.subscribe((type) => {
      this.isValid.set(null);
      this.validationError.set(null);
      
      // Clear and update validators based on type
      const scheduleCtrl = this.form.get('schedule');
      const intervalCtrl = this.form.get('interval_seconds');
      const runAtCtrl = this.form.get('run_at');
      const sessionModeCtrl = this.form.get('session_mode');
      
      if (type === 'cron') {
        scheduleCtrl?.setValidators([Validators.required]);
        intervalCtrl?.clearValidators();
        intervalCtrl?.setValue(60);
        runAtCtrl?.clearValidators();
        runAtCtrl?.setValue('');
      } else if (type === 'interval') {
        scheduleCtrl?.clearValidators();
        scheduleCtrl?.setValue('');
        intervalCtrl?.setValidators([Validators.required, Validators.min(1)]);
        runAtCtrl?.clearValidators();
        runAtCtrl?.setValue('');
      } else if (type === 'one-time') {
        scheduleCtrl?.clearValidators();
        scheduleCtrl?.setValue('');
        intervalCtrl?.clearValidators();
        intervalCtrl?.setValue(60);
        runAtCtrl?.setValidators([Validators.required]);
        // One-time schedules always create a new session
        sessionModeCtrl?.setValue('new_session');
      }
      
      scheduleCtrl?.updateValueAndValidity();
      intervalCtrl?.updateValueAndValidity();
      runAtCtrl?.updateValueAndValidity();
    });
  }

  protected get selectedType(): string {
    return this.form.get('type')?.value || 'cron';
  }

  protected get isSessionModeEnabled(): boolean {
    const type = this.form.get('type')?.value;
    // Enable session mode selector only when a schedule type is selected
    return !!type;
  }

  protected get isReuseSessionDisabled(): boolean {
    return this.selectedType === 'one-time';
  }

  protected get showOneTimeSessionHint(): boolean {
    return this.selectedType === 'one-time';
  }

  protected handleClose(): void {
    this.dialogRef.close();
  }

  protected handleValidate(): void {
    const config = this.buildScheduleConfig();
    if (!config) return;
    
    this.isValidating.set(true);
    this.isValid.set(null);
    this.validationError.set(null);
    
    this.schedulerService.validateSchedule(config).subscribe({
      next: (response: ValidationResponse) => {
        this.isValid.set(response.valid);
        if (!response.valid && response.error) {
          this.validationError.set(response.error);
        }
        this.isValidating.set(false);
      },
      error: (err) => {
        console.error('Validation failed:', err);
        this.isValid.set(false);
        this.validationError.set(err.error?.error || 'Validation failed');
        this.isValidating.set(false);
      }
    });
  }

  protected async handleSubmit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.isLoading.set(true);

    try {
      const result: ScheduleCreateDialogResult = {
        name: this.form.value.name,
        agent: this.form.value.agent,
        message: this.form.value.message,
        project: this.form.value.project || undefined,
        timezone: this.form.value.timezone || 'UTC',
        schedule_type: this.form.value.type,
        session_mode: this.form.value.session_mode
      };

      if (this.form.value.type === 'cron') {
        result.schedule = this.form.value.schedule;
      } else if (this.form.value.type === 'interval') {
        result.interval_seconds = this.form.value.interval_seconds;
      } else if (this.form.value.type === 'one-time') {
        result.run_at = this.form.value.run_at;
      }

      this.dialogRef.close(result);
    } catch (err) {
      console.error('Failed to create schedule:', err);
      this.snackBar.open(
        err instanceof Error ? err.message : 'Failed to create schedule',
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

  private buildScheduleConfig() {
    const type = this.form.value.type;
    
    const config: any = {
      agent: this.form.value.agent,
      message: this.form.value.message,
      session_mode: this.form.value.session_mode
    };

    if (type === 'cron') {
      config.schedule = this.form.value.schedule;
    } else if (type === 'interval') {
      config.interval_seconds = this.form.value.interval_seconds;
    } else if (type === 'one-time') {
      config.run_at = this.form.value.run_at;
    }

    if (this.form.value.timezone) {
      config.timezone = this.form.value.timezone;
    }

    if (this.form.value.project) {
      config.project = this.form.value.project;
    }

    return config;
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || this.form.invalid;
  }

  protected getAgentDisplayName(agent: Agent): string {
    return `${agent.icon} ${agent.name}`;
  }

  protected getIntervalDisplay(seconds: number): string {
    if (seconds < 60) {
      return `${seconds} seconds`;
    } else if (seconds < 3600) {
      const minutes = Math.floor(seconds / 60);
      return `${minutes} minute${minutes > 1 ? 's' : ''}`;
    } else if (seconds < 86400) {
      const hours = Math.floor(seconds / 3600);
      return `${hours} hour${hours > 1 ? 's' : ''}`;
    } else {
      const days = Math.floor(seconds / 86400);
      return `${days} day${days > 1 ? 's' : ''}`;
    }
  }

  protected getCronHint(): string {
    const cron = this.form.get('schedule')?.value;
    if (!cron) return '';
    
    // Simple cron hint generator
    const parts = cron.split(' ');
    if (parts.length !== 5) return 'Invalid cron expression';
    
    const [minute, hour, day, month, dow] = parts;
    
    if (minute === '0' && hour === '*' && day === '*' && month === '*' && dow === '*') {
      return 'Every hour at minute 0';
    }
    if (minute === '*' && hour === '*' && day === '*' && month === '*' && dow === '*') {
      return 'Every minute';
    }
    if (minute === '0' && hour === '0' && day === '*' && month === '*' && dow === '*') {
      return 'Every day at midnight';
    }
    if (minute === '0' && hour === '9' && day === '*' && month === '*' && dow === '1-5') {
      return 'Weekdays at 9:00 AM';
    }
    
    return '';
  }
}
