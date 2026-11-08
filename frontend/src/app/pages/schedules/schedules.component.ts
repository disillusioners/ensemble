import { Component, signal, computed, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { SchedulerService } from '../../services/scheduler.service';
import { ApiService } from '../../services/api.service';
import { ScheduleCardComponent } from '../../components/schedule-card/schedule-card.component';
import { ScheduleDetailDrawerComponent } from '../../components/schedule-detail-drawer/schedule-detail-drawer.component';
import { ScheduleCreateDialogComponent, ScheduleCreateDialogResult } from '../../components/schedule-create-dialog/schedule-create-dialog.component';
import { SearchableSelectComponent, SearchableSelectOption } from '../../components';
import { Schedule, ScheduleStatus, ScheduleType } from '../../models/scheduler.model';
import {
  readShowTerminalPreference,
  splitByTerminalVisibility,
  writeShowTerminalPreference,
} from '../../models/terminal-visibility.model';
import { Agent } from '../../models';

type ActionType = 'start' | 'stop';

@Component({
  selector: 'app-schedules',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatSidenavModule,
    MatSnackBarModule,
    MatDialogModule,
    MatTooltipModule,
    MatCheckboxModule,
    ScheduleCardComponent,
    ScheduleDetailDrawerComponent,
    SearchableSelectComponent
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

  // ── Terminal-row visibility ("Show completed & cancelled") ──────────
  // Default-ACTIVE view: terminal schedules (stopped/cancelled one-shot
  // alarms) are hidden unless the user opts in. EAGER initialization:
  // the persisted preference is read at field-init (no restore effect,
  // no guard flag, no one-render flash — the very first render already
  // reflects the stored choice). Same shared localStorage key as the
  // /sources surface (terminal-visibility.model).
  readonly showTerminal = signal(readShowTerminalPreference(localStorage));

  constructor() {
    // The schedules fetch REACTS to the toggle (include_cancelled
    // gating): every flip re-issues the request — ON →
    // ``?include_cancelled=true``, OFF → bare default request. The
    // first effect run IS the initial load, and eager init above means
    // that first request already respects the stored preference.
    effect(() => {
      this.loadSchedules();
    });
  }

  // ONE pass over the UNFILTERED list drives both the hidden count and
  // the active-only subset; the stats badges below keep reading
  // `schedules()` directly, so counts stay truthful for ALL rows.
  private readonly terminalSplit = computed(() =>
    splitByTerminalVisibility(this.schedules(), this.showTerminal()),
  );
  readonly hiddenTerminalCount = computed<number>(() => this.terminalSplit().hiddenTerminal);

  // Computed values
  readonly filteredSchedules = computed((): Schedule[] => {
    // Stage 1 — terminal visibility (BEFORE the chip/type filters, so
    // the default active-only view applies unless the user opted in).
    let filtered: Schedule[] = this.terminalSplit().visible;

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

  /**
   * Emptiness caused by the TERMINAL stage: the filtered view is empty
   * ONLY because the active-only terminal filter hid the rows that
   * would otherwise match the current chip/type filters (toggle OFF,
   * but matching rows EXIST in the full list). In exactly that case
   * "clear all filters" cannot reveal them — only the toggle can — so
   * the empty copy points at the toggle instead. A genuine filter miss
   * (no row matches even before the terminal stage) keeps the original
   * copy.
   */
  readonly isTerminalStageEmpty = computed((): boolean => {
    if (this.showTerminal()) return false;
    if (this.filteredSchedules().length > 0) return false;
    const statusVal = this.statusFilter();
    const typeVal = this.typeFilter();
    return this.schedules().some((s: Schedule) => {
      if (statusVal !== 'all' && s.status !== statusVal) return false;
      if (typeVal !== 'all' && s.config.type !== typeVal) return false;
      return true;
    });
  });

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

  /**
   * Type filter options mapped to `SearchableSelectOption` shape.
   * The static `typeOptions` shape already matches, but we wrap
   * the mapping in a computed signal so the template can bind
   * `SearchableSelectOption<ScheduleType | 'all'>[]` directly.
   */
  readonly typeSelectOptions = computed<SearchableSelectOption<ScheduleType | 'all'>[]>(
    () => this.typeOptions.map((opt) => ({ value: opt.value, label: opt.label })),
  );

  ngOnInit(): void {
    // The schedule list is loaded by the constructor effect (its first
    // run IS the initial load — keeps the fetch single-sourced and
    // reactive to the toggle). Agents are a plain one-shot load.
    this.loadAgents();
  }

  ngOnDestroy(): void {
    this.schedulerService.clearError();
  }

  // ── Response supersede (flip-race guard) ─────────────────────────────
  // A fast ON→OFF flip re-issues the fetch while the previous request is
  // still in flight; unguarded, the stale ``include_cancelled`` response
  // can land LAST and transiently overcount the "N hidden" line. The
  // token below is captured at subscribe-time and EVERY delivery is
  // checked before any state write. The shared list itself is written
  // by the service's tap (upstream of this handler), so a superseded
  // delivery that already landed is synchronously re-asserted back to
  // the winning page within the same emission pass — signal/effect
  // observers only ever see the final value.
  private loadToken = 0;
  private acceptedRows: Schedule[] | null = null;

  private loadSchedules(): void {
    this.loading.set(true);
    this.error.set(null);

    // include_cancelled gating: the toggle decides whether the fetch
    // asks the backend for cancelled rows (ON → param, OFF → bare
    // request). Fetched rows (cancelled included when present) flow
    // through the existing client-side terminal filter below, so the
    // visible subset, hidden-count line and stats stay truthful.
    const token = ++this.loadToken;
    this.schedulerService.listSchedules(this.showTerminal()).subscribe({
      next: (rows) => {
        if (token !== this.loadToken) {
          // Superseded: drop it. The service tap already wrote this
          // stale page into the shared signal — re-assert the winner's
          // page so the stale payload never sticks.
          if (this.acceptedRows) this.schedules.set(this.acceptedRows);
          return;
        }
        this.acceptedRows = rows;
        this.loading.set(false);
      },
      error: (err: Error) => {
        if (token !== this.loadToken) return; // superseded — never clobber fresher state
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
    // Re-issue through the toggle-aware load so a refresh never
    // silently re-hides the cancelled rows the user opted into.
    this.loadSchedules();
  }

  protected onStatusFilterChange(status: ScheduleStatus | 'all'): void {
    this.statusFilter.set(status);
  }

  protected onTypeFilterChange(type: ScheduleType | 'all'): void {
    this.typeFilter.set(type);
  }

  protected onShowTerminalChange(checked: boolean): void {
    this.showTerminal.set(checked);
    writeShowTerminalPreference(localStorage, checked);
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

  protected getAgentDisplayName(agentId: string): string {
    const agent = this.agents().find(a => a.agent_id === agentId);
    return agent ? `${agent.icon} ${agent.name}` : agentId;
  }
}
