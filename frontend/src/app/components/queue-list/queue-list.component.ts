import { Component, input, output, inject, signal, computed, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { QueueService } from '../../services/queue.service';
import { JobQueue, getQueueStatusColor, getQueueStatusLabel, getQueueTypeIcon, getQueueTypeLabel } from '../../models/job-queue.model';
import { QueueCreateDialogComponent, QueueCreateDialogResult } from '../queue-create-dialog/queue-create-dialog.component';

@Component({
  selector: 'app-queue-list',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatTooltipModule,
    MatProgressSpinnerModule,
    MatDialogModule,
    MatSnackBarModule
  ],
  templateUrl: './queue-list.component.html',
  styleUrl: './queue-list.component.scss'
})
export class QueueListComponent {
  private readonly queueService = inject(QueueService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  
  // Inputs
  projectId = input<string | null>(null);
  selectedQueueId = input<string | null>(null);
  
  // Outputs
  queueSelected = output<string | null>();
  queueChanged = output<void>();

  // State
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // Computed values
  readonly queues = computed(() => this.queueService.queues());

  // Track which queues are being operated on
  private readonly operatingQueueIds = signal<Set<string>>(new Set());

  constructor() {
    // Reload queues when project changes
    effect(() => {
      const projectId = this.projectId();
      if (projectId) {
        this.loadQueues(projectId);
      } else {
        this.queueService.queues.set([]);
      }
    });
  }

  private loadQueues(projectId: string): void {
    this.loading.set(true);
    this.error.set(null);

    this.queueService.listQueues(projectId).subscribe({
      next: () => {
        this.loading.set(false);
      },
      error: (err) => {
        console.error('Failed to load queues:', err);
        this.error.set(err.message || 'Failed to load queues');
        this.loading.set(false);
      }
    });
  }

  protected isQueueOperating(queueId: string): boolean {
    return this.operatingQueueIds().has(queueId);
  }

  protected onQueueClick(queue: JobQueue): void {
    if (this.selectedQueueId() === queue.queue_id) {
      // Deselect - show all jobs
      this.queueSelected.emit(null);
    } else {
      this.queueSelected.emit(queue.queue_id);
    }
  }

  protected onRefresh(): void {
    const projectId = this.projectId();
    if (projectId) {
      this.queueService.refreshQueues(projectId);
    }
  }

  protected onStartQueue(queue: JobQueue, event: Event): void {
    event.stopPropagation();
    const projectId = this.projectId();
    if (!projectId || this.isQueueOperating(queue.queue_id)) return;

    this.operatingQueueIds.update(set => {
      const newSet = new Set(set);
      newSet.add(queue.queue_id);
      return newSet;
    });

    this.queueService.startQueue(projectId, queue.queue_id).subscribe({
      next: () => {
        this.operatingQueueIds.update(set => {
          const newSet = new Set(set);
          newSet.delete(queue.queue_id);
          return newSet;
        });
        this.queueChanged.emit();
        this.snackBar.open('Queue started', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
      },
      error: (err) => {
        this.operatingQueueIds.update(set => {
          const newSet = new Set(set);
          newSet.delete(queue.queue_id);
          return newSet;
        });
        this.snackBar.open('Failed to start queue', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onStopQueue(queue: JobQueue, event: Event): void {
    event.stopPropagation();
    const projectId = this.projectId();
    if (!projectId || this.isQueueOperating(queue.queue_id)) return;

    this.operatingQueueIds.update(set => {
      const newSet = new Set(set);
      newSet.add(queue.queue_id);
      return newSet;
    });

    this.queueService.stopQueue(projectId, queue.queue_id).subscribe({
      next: () => {
        this.operatingQueueIds.update(set => {
          const newSet = new Set(set);
          newSet.delete(queue.queue_id);
          return newSet;
        });
        this.queueChanged.emit();
        this.snackBar.open('Queue paused', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
      },
      error: (err) => {
        this.operatingQueueIds.update(set => {
          const newSet = new Set(set);
          newSet.delete(queue.queue_id);
          return newSet;
        });
        this.snackBar.open('Failed to pause queue', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onDeleteQueue(queue: JobQueue, event: Event): void {
    event.stopPropagation();
    const projectId = this.projectId();
    if (!projectId || this.isQueueOperating(queue.queue_id)) return;

    if (!confirm(`Delete queue "${queue.queue_name}"? This action cannot be undone.`)) {
      return;
    }

    this.operatingQueueIds.update(set => {
      const newSet = new Set(set);
      newSet.add(queue.queue_id);
      return newSet;
    });

    this.queueService.deleteQueue(projectId, queue.queue_id).subscribe({
      next: () => {
        this.operatingQueueIds.update(set => {
          const newSet = new Set(set);
          newSet.delete(queue.queue_id);
          return newSet;
        });
        this.queueChanged.emit();
        this.snackBar.open('Queue deleted', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
      },
      error: (err) => {
        this.operatingQueueIds.update(set => {
          const newSet = new Set(set);
          newSet.delete(queue.queue_id);
          return newSet;
        });
        this.snackBar.open('Failed to delete queue', 'Close', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onCreateQueue(): void {
    const projectId = this.projectId();
    if (!projectId) return;

    const dialogRef = this.dialog.open(QueueCreateDialogComponent, {
      data: { projectId },
      panelClass: 'dark-modal-panel',
      width: '480px'
    });

    dialogRef.afterClosed().subscribe((result: QueueCreateDialogResult | undefined) => {
      if (result) {
        this.queueService.createQueue(projectId, {
          queue_name: result.queue_name,
          queue_type: result.queue_type,
          concurrency_limit: result.concurrency_limit,
          description: result.description
        }).subscribe({
          next: () => {
            this.queueChanged.emit();
            this.snackBar.open('Queue created successfully', 'Close', {
              duration: 3000,
              panelClass: 'success-snackbar'
            });
          },
          error: (err) => {
            this.snackBar.open('Failed to create queue', 'Close', {
              duration: 5000,
              panelClass: 'error-snackbar'
            });
          }
        });
      }
    });
  }

  protected isSelected(queue: JobQueue): boolean {
    return this.selectedQueueId() === queue.queue_id;
  }

  protected canDeleteQueue(queue: JobQueue): boolean {
    return !queue.is_system;
  }

  protected getStatusColor(paused: boolean): string {
    return getQueueStatusColor(paused);
  }

  protected getStatusLabel(paused: boolean): string {
    return getQueueStatusLabel(paused);
  }

  protected getTypeIcon(type: 'fifo' | 'parallel'): string {
    return getQueueTypeIcon(type);
  }

  protected getTypeLabel(type: 'fifo' | 'parallel'): string {
    return getQueueTypeLabel(type);
  }

  protected trackByQueueId(index: number, queue: JobQueue): string {
    return queue.queue_id;
  }
}
