import { Component, input, output, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Job, JobStatus, getPriorityColor, getStatusColor } from '../../models/job.model';

@Component({
  selector: 'app-job-card',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatCardModule,
    MatChipsModule,
    MatIconModule,
    MatExpansionModule,
    MatTooltipModule
  ],
  templateUrl: './job-card.component.html',
  styleUrl: './job-card.component.scss'
})
export class JobCardComponent {
  job = input.required<Job>();
  projectPaused = input<boolean>(false);

  // Action outputs
  cancel = output<void>();
  retry = output<void>();
  viewDetails = output<void>();

  // Internal state
  expanded = signal(false);

  // Computed values
  priorityColor = computed(() => getPriorityColor(this.job().priority));
  statusColor = computed(() => getStatusColor(this.job().status));

  priorityLabel = computed(() => `P${this.job().priority}`);
  priorityTextColor = computed(() => {
    const color = this.priorityColor();
    // For dark backgrounds, white text works for most colors
    // For amber/yellow, use dark text
    return color === '#F59E0B' ? '#000000' : '#FFFFFF';
  });

  statusIcon = computed(() => {
    const status = this.job().status;
    switch (status) {
      case 'pending': return 'schedule';
      case 'processing': return 'sync';
      case 'completed': return 'check_circle';
      case 'failed': return 'error';
      case 'cancelled': return 'cancel';
      default: return 'help';
    }
  });

  statusLabel = computed(() => {
    const status = this.job().status;
    return status.charAt(0).toUpperCase() + status.slice(1);
  });

  messagePreview = computed(() => {
    const msg = this.job().message;
    return msg.length > 100 ? msg.substring(0, 100) + '...' : msg;
  });

  relativeTime = computed(() => {
    const date = new Date(this.job().created_at);
    return this.getRelativeTime(date);
  });

  canCancel = computed(() => {
    const status = this.job().status;
    return status === 'pending' || status === 'processing';
  });

  canRetry = computed(() => this.job().status === 'failed');

  showPausedBadge = computed(() => {
    return this.job().status === 'pending' && this.projectPaused();
  });

  protected onCancel(): void {
    this.cancel.emit();
  }

  protected onRetry(): void {
    this.retry.emit();
  }

  protected onViewDetails(): void {
    this.viewDetails.emit();
  }

  protected toggleExpanded(): void {
    this.expanded.update(v => !v);
  }

  protected getRelativeTime(date: Date): string {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSecs = Math.floor(diffMs / 1000);
    const diffMins = Math.floor(diffSecs / 60);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffSecs < 60) return 'just now';
    if (diffMins < 60) return `${diffMins} min${diffMins > 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
  }

  protected formatTimestamp(timestamp: string | null): string {
    if (!timestamp) return 'N/A';
    return new Date(timestamp).toLocaleString();
  }
}
