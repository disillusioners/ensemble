import { Component, input, output, computed, inject, signal, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Clipboard } from '@angular/cdk/clipboard';
import { SchedulerService } from '../../services/scheduler.service';
import { SearchableSelectComponent } from '../searchable-select/searchable-select.component';
import type { SearchableSelectOption } from '../searchable-select/searchable-select.component';
import type {
  Schedule,
  ScheduleExecution,
  ScheduleConfiguration,
  ScheduleType,
  SessionMode,
} from '../../models/scheduler.model';

@Component({
  selector: 'app-schedule-detail-drawer',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatChipsModule,
    MatIconModule,
    MatDividerModule,
    MatTooltipModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressSpinnerModule,
    SearchableSelectComponent,
  ],
  templateUrl: './schedule-detail-drawer.component.html',
  styleUrl: './schedule-detail-drawer.component.scss',
})
export class ScheduleDetailDrawerComponent {
  private schedulerService = inject(SchedulerService);
  private clipboard = inject(Clipboard);

  schedule = input<Schedule | null>(null);
  isDrawerMode = input<boolean>(false);

  close = output<void>();
  saved = output<Schedule>();
  deleted = output<Schedule>();

  // Local editing state
  editingName = signal<string>('');
  editingConfig = signal<ScheduleConfiguration | null>(null);
  editingSessionMode = signal<SessionMode>('new_session');

  // Execution history state
  executions = signal<ScheduleExecution[]>([]);
  loadingExecutions = signal(false);
  hasMoreExecutions = signal(true);
  private executionsOffset = signal(0);
  private readonly executionsLimit = 10;

  // UI state
  isEditing = signal(false);
  showDeleteConfirm = signal(false);
  actionLoading = signal(false);

  // Computed properties
  statusColor = computed(() => {
    const schedule = this.schedule();
    if (!schedule) return 'primary';
    switch (schedule.status) {
      case 'running':
        return 'primary';
      case 'stopped':
        return 'warn';
      default:
        return 'primary';
    }
  });

  statusLabel = computed(() => {
    const schedule = this.schedule();
    if (!schedule) return '';
    return schedule.status.charAt(0).toUpperCase() + schedule.status.slice(1);
  });

  canStart = computed(() => {
    const schedule = this.schedule();
    return schedule?.status === 'stopped';
  });

  canStop = computed(() => {
    const schedule = this.schedule();
    return schedule?.status === 'running';
  });

  hasChanges = computed(() => {
    const schedule = this.schedule();
    const config = this.editingConfig();
    if (!schedule || !config) return false;
    return (
      this.editingName() !== schedule.name ||
      config.type !== schedule.config.type ||
      config.expression !== schedule.config.expression ||
      config.interval_seconds !== schedule.config.interval_seconds ||
      config.run_at !== schedule.config.run_at ||
      config.agent !== schedule.config.agent ||
      config.message !== schedule.config.message ||
      config.timezone !== schedule.config.timezone ||
      config.project !== schedule.config.project ||
      this.editingSessionMode() !== (schedule.config.session_mode || 'new_session')
    );
  });

  scheduleTypeLabel = computed(() => {
    const config = this.editingConfig();
    if (!config) return 'Unknown';
    return config.type.charAt(0).toUpperCase() + config.type.slice(1);
  });

  // Static source of truth for the schedule-type dropdown. Mapped to the
  // canonical {value,label} shape required by app-searchable-select via the
  // computed signal below so the input reference stays stable per render.
  private static readonly SCHEDULE_TYPE_OPTIONS: ReadonlyArray<{
    value: ScheduleType;
    label: string;
  }> = [
    { value: 'cron', label: 'Cron' },
    { value: 'interval', label: 'Interval' },
    { value: 'one-time', label: 'One-time' },
  ];

  readonly scheduleTypeOptions = computed<SearchableSelectOption<ScheduleType>[]>(
    () =>
      ScheduleDetailDrawerComponent.SCHEDULE_TYPE_OPTIONS.map((o) => ({
        value: o.value,
        label: o.label,
      })),
  );

  intervalDisplay = computed(() => {
    const config = this.editingConfig();
    if (!config?.interval_seconds) return '';
    const seconds = config.interval_seconds;
    if (seconds < 60) return `${seconds} seconds`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes`;
    return `${Math.floor(seconds / 3600)} hours`;
  });

  // Watch for schedule changes to initialize editing state
  constructor() {
    effect(() => {
      const schedule = this.schedule();
      if (schedule) {
        this.editingName.set(schedule.name);
        this.editingConfig.set({ ...schedule.config });
        this.editingSessionMode.set(schedule.config.session_mode || 'new_session');
        this.executions.set([]);
        this.executionsOffset.set(0);
        this.hasMoreExecutions.set(true);
        this.isEditing.set(false);
      }
    });
  }

  loadExecutions(): void {
    const schedule = this.schedule();
    if (!schedule || this.loadingExecutions()) return;

    this.loadingExecutions.set(true);
    this.schedulerService.getExecutions(schedule.id, this.executionsLimit, this.executionsOffset()).subscribe({
      next: (execs) => {
        this.executions.update((current) =>
          this.executionsOffset() === 0 ? execs : [...current, ...execs]
        );
        this.hasMoreExecutions.set(execs.length === this.executionsLimit);
        this.loadingExecutions.set(false);
      },
      error: () => {
        this.loadingExecutions.set(false);
      },
    });
  }

  loadMoreExecutions(): void {
    this.executionsOffset.update((offset) => offset + this.executionsLimit);
    this.loadExecutions();
  }

  onOpenDrawer(): void {
    this.loadExecutions();
  }

  onClose(): void {
    this.close.emit();
  }

  onStartEdit(): void {
    const schedule = this.schedule();
    if (schedule) {
      this.editingName.set(schedule.name);
      this.editingConfig.set({ ...schedule.config });
      this.isEditing.set(true);
    }
  }

  onCancelEdit(): void {
    const schedule = this.schedule();
    if (schedule) {
      this.editingName.set(schedule.name);
      this.editingConfig.set({ ...schedule.config });
      this.editingSessionMode.set(schedule.config.session_mode || 'new_session');
    }
    this.isEditing.set(false);
  }

  onSave(): void {
    const schedule = this.schedule();
    const config = this.editingConfig();
    if (!schedule || !config) return;

    this.actionLoading.set(true);

    // Update config with session_mode
    const updatedConfig = {
      ...config,
      session_mode: this.editingSessionMode(),
    };
    
    this.schedulerService.updateSchedule(schedule.id, {
      name: this.editingName(),
      config: updatedConfig,
    }).subscribe({
      next: (updated) => {
        this.saved.emit(updated);
        this.isEditing.set(false);
        this.actionLoading.set(false);
      },
      error: () => {
        this.actionLoading.set(false);
      },
    });
  }

  onStart(): void {
    const schedule = this.schedule();
    if (!schedule) return;

    this.actionLoading.set(true);
    this.schedulerService.startSchedule(schedule.id).subscribe({
      next: (updated) => {
        this.saved.emit(updated);
        this.actionLoading.set(false);
      },
      error: () => {
        this.actionLoading.set(false);
      },
    });
  }

  onStop(): void {
    const schedule = this.schedule();
    if (!schedule) return;

    this.actionLoading.set(true);
    this.schedulerService.stopSchedule(schedule.id).subscribe({
      next: (updated) => {
        this.saved.emit(updated);
        this.actionLoading.set(false);
      },
      error: () => {
        this.actionLoading.set(false);
      },
    });
  }

  onTrigger(): void {
    const schedule = this.schedule();
    if (!schedule) return;

    this.actionLoading.set(true);
    this.schedulerService.triggerSchedule(schedule.id).subscribe({
      next: () => {
        // Refresh executions after trigger
        this.executions.set([]);
        this.executionsOffset.set(0);
        this.loadExecutions();
        this.actionLoading.set(false);
      },
      error: () => {
        this.actionLoading.set(false);
      },
    });
  }

  onDelete(): void {
    const schedule = this.schedule();
    if (!schedule) return;

    this.actionLoading.set(true);
    this.schedulerService.deleteSchedule(schedule.id).subscribe({
      next: () => {
        this.deleted.emit(schedule);
        this.showDeleteConfirm.set(false);
        this.actionLoading.set(false);
      },
      error: () => {
        this.actionLoading.set(false);
      },
    });
  }

  onCopyId(): void {
    const schedule = this.schedule();
    if (schedule?.id) {
      this.clipboard.copy(schedule.id);
    }
  }

  // Config field update methods for template binding
  onTypeChange(type: ScheduleType): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, type };
      });
    }
  }

  onExpressionChange(expression: string): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, expression };
      });
    }
  }

  onIntervalChange(intervalStr: string): void {
    const config = this.editingConfig();
    const interval = parseInt(intervalStr, 10);
    if (config && !isNaN(interval)) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, interval_seconds: interval };
      });
    }
  }

  onRunAtChange(runAt: string): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, run_at: runAt };
      });
    }
  }

  onAgentChange(agent: string): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, agent };
      });
    }
  }

  onMessageChange(message: string): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, message };
      });
    }
  }

  onTimezoneChange(timezone: string): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, timezone };
      });
    }
  }

  onProjectChange(project: string): void {
    const config = this.editingConfig();
    if (config) {
      this.editingConfig.update((c) => {
        if (!c) return c;
        return { ...c, project };
      });
    }
  }

  onSessionModeChange(sessionMode: SessionMode): void {
    this.editingSessionMode.set(sessionMode);
  }

  // Session mode helper getters
  get isSessionModeEnabled(): boolean {
    const config = this.editingConfig();
    // Enable session mode selector only when a schedule type is selected
    return !!config?.type;
  }

  get isReuseSessionDisabled(): boolean {
    const config = this.editingConfig();
    return config?.type === 'one-time';
  }

  get showOneTimeSessionHint(): boolean {
    const config = this.editingConfig();
    return config?.type === 'one-time';
  }

  getSessionModeLabel(mode: SessionMode | undefined): string {
    return mode === 'reuse_session' ? 'Reuse Instance' : 'New Instance';
  }

  getSessionModeDescription(mode: SessionMode | undefined): string {
    return mode === 'reuse_session' 
      ? 'Continue from previous run' 
      : 'Start a fresh conversation instance';
  }

  formatDate(dateStr?: string | null): string {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleString();
  }

  formatExecutionDuration(execution: ScheduleExecution): string | null {
    if (!execution.started_at || !execution.completed_at) return null;
    const start = new Date(execution.started_at).getTime();
    const end = new Date(execution.completed_at).getTime();
    const diffMs = end - start;
    if (diffMs < 0) return null;

    const seconds = Math.floor(diffMs / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);

    if (hours > 0) {
      return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
    } else if (minutes > 0) {
      return `${minutes}m ${seconds % 60}s`;
    } else {
      return `${seconds}s`;
    }
  }

  getExecutionStatusClass(status: string): string {
    return `status-${status}`;
  }
}
