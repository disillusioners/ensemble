import { Component, signal, computed, inject, OnInit, OnDestroy } from '@angular/core';
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
import { SchedulerService } from '../../services/scheduler.service';
import { ApiService } from '../../services/api.service';
import { ScheduleCardComponent } from '../../components/schedule-card/schedule-card.component';
import { ScheduleDetailDrawerComponent } from '../../components/schedule-detail-drawer/schedule-detail-drawer.component';
import { ScheduleCreateDialogComponent, ScheduleCreateDialogResult } from '../../components/schedule-create-dialog/schedule-create-dialog.component';
import { Schedule, ScheduleStatus, ScheduleType } from '../../models/scheduler.model';
import { Agent } from '../../models';

type ActionType = 'start' | 'stop';

@Component({
  selector: 'app-schedules',
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
    ScheduleCardComponent,
    ScheduleDetailDrawerComponent
  ],
  templateUrl: './schedules.component.html',
  styleUrl: './schedules.component.scss'
})
export class SchedulesComponent implements OnInit, OnDestroy {
  private readonly schedulerService = inject(SchedulerService);
  private readonly api = inject(ApiService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);

  // Signals for state
  readonly schedules = this.schedulerService.schedules;
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly agents = signal<Agent[]>([]);
  readonly selectedSchedule = signal<Schedule | null>(null);
  readonly drawerOpen = signal(false);
  
  // Filter signals
  readonly statusFilter = signal<ScheduleStatus | 'all'>('all');
  readonly typeFilter = signal<ScheduleType | 'all'>('all');

  // Computed values
  readonly filteredSchedules = computed((): Schedule[] => {
    let filtered: Schedule[] = this.schedules();
    
    const statusVal = this.statusFilter();
    if (statusVal !== 'all') {
      filtered = filtered.filter((s: Schedule) => s.status === statusVal);
    }
    
    const typeVal = this.typeFilter();
    if (typeVal !== 'all') {
      filtered = filtered.filter((s: Schedule) => s.config.type === typeVal);
    }
    
    return filtered;
  });

  readonly hasSchedules = computed((): boolean => this.filteredSchedules().length > 0);
  readonly isEmptyState = computed((): boolean => 
    !this.loading() && this.schedules().length === 0 && !this.error()
  );
  readonly isFilteredEmpty = computed((): boolean => 
    !this.loading() && this.filteredSchedules().length === 0 && this.schedules().length > 0
  );

  // Computed stats
  readonly activeCount = computed((): number => 
    this.schedules().filter((s: Schedule) => s.status === 'running').length
  );
  readonly stoppedCount = computed((): number => 
    this.schedules().filter((s: Schedule) => s.status === 'stopped').length
  );
  readonly totalCount = computed((): number => this.schedules().length);

  // Status filter options
  readonly statusOptions: { value: ScheduleStatus | 'all'; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'running', label: 'Running' },
    { value: 'stopped', label: 'Stopped' }
  ];

  // Type filter options
  readonly typeOptions: { value: ScheduleType | 'all'; label: string }[] = [
    { value: 'all', label: 'All Types' },
    { value: 'cron', label: 'Cron' },
    { value: 'interval', label: 'Interval' },
    { value: 'one-time', label: 'One-time' }
  ];

  ngOnInit(): void {
    this.loadSchedules();
    this.loadAgents();
  }

  ngOnDestroy(): void {
    this.schedulerService.clearError();
  }

  private loadSchedules(): void {
    this.loading.set(true);
    this.error.set(null);

    this.schedulerService.listSchedules().subscribe({
      next: () => {
        this.loading.set(false);
      },
      error: (err: Error) => {
        console.error('Failed to load schedules:', err);
        this.error.set(err.message || 'Failed to load schedules');
        this.loading.set(false);
      }
    });
  }

  private loadAgents(): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
      },
      error: (err: Error) => {
        console.error('Failed to load agents:', err);
      }
    });
  }

  protected onRefresh(): void {
    this.schedulerService.refreshSchedules();
  }

  protected onStatusFilterChange(status: ScheduleStatus | 'all'): void {
    this.statusFilter.set(status);
  }

  protected onTypeFilterChange(type: ScheduleType | 'all'): void {
    this.typeFilter.set(type);
  }

  protected onClearFilters(): void {
    this.statusFilter.set('all');
    this.typeFilter.set('all');
  }

  protected hasActiveFilters(): boolean {
    return this.statusFilter() !== 'all' || this.typeFilter() !== 'all';
  }

  protected onOpenCreateDialog(): void {
    const dialogRef = this.dialog.open(ScheduleCreateDialogComponent, {
      width: '600px',
      panelClass: 'dark-modal-panel',
      data: {}
    });

    dialogRef.afterClosed().subscribe((result: ScheduleCreateDialogResult | undefined) => {
      if (result) {
        this.createSchedule(result);
      }
    });
  }

  private createSchedule(data: ScheduleCreateDialogResult): void {
    // Build schedule configuration based on type
    const config: Record<string, unknown> = {
      agent: data.agent,
      message: data.message,
      timezone: data.timezone
    };

    if (data.schedule_type === 'cron' && data.schedule) {
      config['schedule'] = data.schedule;
      config['type'] = 'cron';
    } else if (data.schedule_type === 'interval' && data.interval_seconds) {
      config['interval_seconds'] = data.interval_seconds;
      config['type'] = 'interval';
    } else if (data.schedule_type === 'one-time' && data.run_at) {
      config['run_at'] = data.run_at;
      config['type'] = 'one-time';
    }

    if (data.project) {
      config['project'] = data.project;
    }

    // Use sources API to create a scheduler source
    this.api.createSource({
      source_id: `scheduler-${Date.now()}`,
      source_type: 'scheduler',
      name: data.name,
      config,
      enabled: true
    }).subscribe({
      next: () => {
        this.snackBar.open('Schedule created successfully', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.loadSchedules();
      },
      error: (err: Error) => {
        console.error('Failed to create schedule:', err);
        this.snackBar.open(
          err.message || 'Failed to create schedule',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onEditSchedule(schedule: Schedule): void {
    const dialogRef = this.dialog.open(ScheduleCreateDialogComponent, {
      width: '600px',
      panelClass: 'dark-modal-panel',
      data: {
        editMode: true,
        scheduleId: schedule.id,
        name: schedule.name,
        agent: schedule.config.agent,
        message: schedule.config.message,
        project: schedule.config.project,
        timezone: schedule.config.timezone
      }
    });

    dialogRef.afterClosed().subscribe((result: ScheduleCreateDialogResult | undefined) => {
      if (result) {
        this.updateSchedule(schedule.id, result);
      }
    });
  }

  private updateSchedule(scheduleId: string, data: ScheduleCreateDialogResult): void {
    const config: Record<string, unknown> = {
      agent: data.agent,
      message: data.message,
      timezone: data.timezone
    };

    if (data.schedule_type === 'cron' && data.schedule) {
      config['schedule'] = data.schedule;
      config['type'] = 'cron';
    } else if (data.schedule_type === 'interval' && data.interval_seconds) {
      config['interval_seconds'] = data.interval_seconds;
      config['type'] = 'interval';
    } else if (data.schedule_type === 'one-time' && data.run_at) {
      config['run_at'] = data.run_at;
      config['type'] = 'one-time';
    }

    if (data.project) {
      config['project'] = data.project;
    }

    this.api.updateSource(scheduleId, {
      name: data.name,
      config
    }).subscribe({
      next: () => {
        this.snackBar.open('Schedule updated successfully', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.loadSchedules();
      },
      error: (err: Error) => {
        console.error('Failed to update schedule:', err);
        this.snackBar.open(
          err.message || 'Failed to update schedule',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onDeleteSchedule(schedule: Schedule): void {
    if (!confirm(`Are you sure you want to delete "${schedule.name}"?`)) {
      return;
    }

    this.schedulerService.deleteSchedule(schedule.id).subscribe({
      next: () => {
        this.snackBar.open('Schedule deleted', 'Close', {
          duration: 3000
        });
        if (this.selectedSchedule()?.id === schedule.id) {
          this.onCloseDrawer();
        }
      },
      error: (err: Error) => {
        console.error('Failed to delete schedule:', err);
        this.snackBar.open(
          err.message || 'Failed to delete schedule',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onTriggerSchedule(schedule: Schedule): void {
    this.schedulerService.triggerSchedule(schedule.id).subscribe({
      next: (response) => {
        this.snackBar.open(
          `Schedule triggered. Execution ID: ${response.execution_id}`,
          'Close',
          {
            duration: 5000,
            panelClass: 'success-snackbar'
          }
        );
        // Refresh to get updated last_run_at
        this.loadSchedules();
      },
      error: (err: Error) => {
        console.error('Failed to trigger schedule:', err);
        this.snackBar.open(
          err.message || 'Failed to trigger schedule',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onToggleScheduleStatus(event: { schedule: Schedule; action: ActionType }): void {
    const { schedule, action } = event;
    
    if (action === 'start') {
      this.schedulerService.startSchedule(schedule.id).subscribe({
        next: () => {
          this.snackBar.open('Schedule started', 'Close', { duration: 3000 });
        },
        error: (err: Error) => {
          console.error('Failed to start schedule:', err);
          this.snackBar.open(
            err.message || 'Failed to start schedule',
            'Dismiss',
            { duration: 5000, panelClass: 'error-snackbar' }
          );
        }
      });
    } else if (action === 'stop') {
      this.schedulerService.stopSchedule(schedule.id).subscribe({
        next: () => {
          this.snackBar.open('Schedule stopped', 'Close', { duration: 3000 });
        },
        error: (err: Error) => {
          console.error('Failed to stop schedule:', err);
          this.snackBar.open(
            err.message || 'Failed to stop schedule',
            'Dismiss',
            { duration: 5000, panelClass: 'error-snackbar' }
          );
        }
      });
    }
  }

  protected onViewScheduleDetails(schedule: Schedule): void {
    this.selectedSchedule.set(schedule);
    this.drawerOpen.set(true);
  }

  protected onCloseDrawer(): void {
    this.drawerOpen.set(false);
    this.selectedSchedule.set(null);
  }

  protected getAgentDisplayName(agentDir: string): string {
    const agent = this.agents().find(a => a.agent_dir === agentDir);
    return agent ? `${agent.icon} ${agent.name}` : agentDir;
  }
}
