import { Component, input, output, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Clipboard } from '@angular/cdk/clipboard';
import type { Job } from '../../models/job.model';

@Component({
  selector: 'app-job-detail-drawer',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatSidenavModule,
    MatChipsModule,
    MatIconModule,
    MatDividerModule,
    MatTooltipModule,
  ],
  templateUrl: './job-detail-drawer.component.html',
  styleUrl: './job-detail-drawer.component.scss',
})
export class JobDetailDrawerComponent {
  private clipboard = inject(Clipboard);

  job = input.required<Job>();
  isDrawerMode = input<boolean>(false);

  close = output<void>();
  cancelJob = output<string>();
  retryJob = output<string>();
  viewSession = output<string>();

  statusColor = computed(() => {
    const status = this.job().status;
    switch (status) {
      case 'pending':
        return 'warn';
      case 'processing':
        return 'accent';
      case 'completed':
        return 'primary';
      case 'failed':
        return 'warn';
      case 'cancelled':
        return 'warn';
      default:
        return 'primary';
    }
  });

  statusLabel = computed(() => {
    const status = this.job().status;
    return status.charAt(0).toUpperCase() + status.slice(1);
  });

  duration = computed(() => {
    const job = this.job();
    if (!job.started_at || !job.completed_at) return null;

    const start = new Date(job.started_at).getTime();
    const end = new Date(job.completed_at).getTime();
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
  });

  canCancel = computed(() => {
    const status = this.job().status;
    return status === 'pending' || status === 'processing';
  });

  canRetry = computed(() => {
    return this.job().status === 'failed';
  });

  hasSession = computed(() => {
    const job = this.job();
    return !!(job.session_id);
  });

  formattedMetadata = computed(() => {
    const metadata = this.job().job_metadata;
    if (!metadata) return null;
    return JSON.stringify(metadata, null, 2);
  });

  priorityLabel = computed(() => `P${this.job().priority}`);

  onClose(): void {
    this.close.emit();
  }

  onCancel(): void {
    this.cancelJob.emit(this.job().job_id);
  }

  onRetry(): void {
    this.retryJob.emit(this.job().job_id);
  }

  onViewSession(): void {
    const sessionId = this.job().session_id;
    if (sessionId) {
      this.viewSession.emit(sessionId);
    }
  }

  onCopyJobId(): void {
    this.clipboard.copy(this.job().job_id);
  }

  formatDate(dateStr?: string | null): string {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleString();
  }
}
