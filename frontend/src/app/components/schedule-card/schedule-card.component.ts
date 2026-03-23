import { Component, input, output, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialogModule } from '@angular/material/dialog';
import {
  Schedule,
  ScheduleStatus,
  ScheduleType,
  getScheduleStatusColor
} from '../../models/scheduler.model';

type ActionType = 'start' | 'stop';

@Component({
  selector: 'app-schedule-card',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatChipsModule,
    MatIconModule,
    MatTooltipModule,
    MatDialogModule
  ],
  templateUrl: './schedule-card.component.html',
  styleUrl: './schedule-card.component.scss'
})
export class ScheduleCardComponent {
  schedule = input.required<Schedule>();

  // Action outputs
  view = output<Schedule>();
  edit = output<Schedule>();
  delete = output<Schedule>();
  trigger = output<Schedule>();
  toggleStatus = output<{ schedule: Schedule; action: ActionType }>();

  // Computed values
  scheduleType = computed((): ScheduleType => {
    return this.schedule().config.type;
  });

  statusColor = computed(() => getScheduleStatusColor(this.schedule().status));

  typeColor = computed(() => {
    switch (this.scheduleType()) {
      case 'cron': return '#3B82F6';      // blue
      case 'interval': return '#8B5CF6'; // purple
      case 'one-time': return '#EC4899';  // pink
      default: return '#9CA3AF';
    }
  });

  statusLabel = computed(() => {
    const status = this.schedule().status;
    return status.charAt(0).toUpperCase() + status.slice(1);
  });

  typeLabel = computed(() => {
    switch (this.scheduleType()) {
      case 'cron': return 'Cron';
      case 'interval': return 'Interval';
      case 'one-time': return 'One-time';
      default: return 'Unknown';
    }
  });

  typeIcon = computed(() => {
    switch (this.scheduleType()) {
      case 'cron': return 'schedule';
      case 'interval': return 'timer';
      case 'one-time': return 'flash_on';
      default: return 'event';
    }
  });

  statusIcon = computed(() => {
    switch (this.schedule().status) {
      case 'running': return 'play_circle';
      case 'stopped': return 'stop_circle';
      default: return 'help';
    }
  });

  canStart = computed(() => {
    const status = this.schedule().status;
    return status === 'stopped';
  });

  canStop = computed(() => this.schedule().status === 'running');

  nextRunDisplay = computed(() => {
    const nextRun = this.schedule().next_run_at;
    if (!nextRun) return null;
    return this.formatDateTime(nextRun);
  });

  lastRunDisplay = computed(() => {
    const lastRun = this.schedule().last_run_at;
    if (!lastRun) return null;
    return this.formatDateTime(lastRun);
  });

  nextRunRelative = computed(() => {
    const nextRun = this.schedule().next_run_at;
    if (!nextRun) return null;
    return this.getRelativeTime(new Date(nextRun));
  });

  lastRunRelative = computed(() => {
    const lastRun = this.schedule().last_run_at;
    if (!lastRun) return null;
    return this.getRelativeTime(new Date(lastRun));
  });

  // Schedule config display
  configDescription = computed(() => {
    const config = this.schedule().config;
    switch (config.type) {
      case 'cron':
        return config.expression || 'No cron expression';
      case 'interval':
        const seconds = config.interval_seconds;
        if (seconds === undefined || seconds === null) return 'Interval';
        if (seconds < 60) return `Every ${seconds}s`;
        if (seconds < 3600) return `Every ${Math.floor(seconds / 60)}m`;
        return `Every ${Math.floor(seconds / 3600)}h`;
      case 'one-time':
        return config.run_at ? this.formatDateTime(config.run_at) : 'Not scheduled';
      default:
        return 'Unknown configuration';
    }
  });

  // Action handlers
  protected onView(): void {
    this.view.emit(this.schedule());
  }

  protected onEdit(): void {
    this.edit.emit(this.schedule());
  }

  protected onDelete(): void {
    this.delete.emit(this.schedule());
  }

  protected onTrigger(): void {
    this.trigger.emit(this.schedule());
  }

  protected onStart(): void {
    this.toggleStatus.emit({ schedule: this.schedule(), action: 'start' });
  }

  protected onStop(): void {
    this.toggleStatus.emit({ schedule: this.schedule(), action: 'stop' });
  }

  // Utility methods
  protected formatDateTime(timestamp: string): string {
    return new Date(timestamp).toLocaleString();
  }

  protected getRelativeTime(date: Date): string {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSecs = Math.floor(diffMs / 1000);
    const diffMins = Math.floor(diffSecs / 60);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffSecs < 0) {
      // Future time
      const futureSecs = Math.abs(diffSecs);
      const futureMins = Math.floor(futureSecs / 60);
      const futureHours = Math.floor(futureMins / 60);
      const futureDays = Math.floor(futureHours / 24);
      if (futureSecs < 60) return 'in a few seconds';
      if (futureMins < 60) return `in ${futureMins} min${futureMins > 1 ? 's' : ''}`;
      if (futureHours < 24) return `in ${futureHours} hour${futureHours > 1 ? 's' : ''}`;
      return `in ${futureDays} day${futureDays > 1 ? 's' : ''}`;
    }

    if (diffSecs < 60) return 'just now';
    if (diffMins < 60) return `${diffMins} min${diffMins > 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
  }
}
