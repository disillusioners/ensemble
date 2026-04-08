import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap, catchError, of, map } from 'rxjs';
import {
  JobQueue,
  JobQueueCreateRequest,
  JobQueueUpdateRequest,
  JobQueueListResponse,
} from '../models/job-queue.model';

@Injectable({
  providedIn: 'root'
})
export class QueueService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/projects';

  // Signals for state
  readonly queues = signal<JobQueue[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/projects/{projectId}/queues
   */
  listQueues(projectId: string): Observable<JobQueue[]> {
    return this.http
      .get<JobQueueListResponse>(`${this.API_BASE}/${encodeURIComponent(projectId)}/queues`)
      .pipe(
        map((response) => response.queues),
        tap((queues) => this.queues.set(queues)),
        catchError((err) => {
          this.error.set(err.message || 'Failed to fetch queues');
          return of([]);
        })
      );
  }

  /**
   * POST /api/projects/{projectId}/queues
   */
  createQueue(projectId: string, data: JobQueueCreateRequest): Observable<JobQueue> {
    return this.http
      .post<JobQueue>(`${this.API_BASE}/${encodeURIComponent(projectId)}/queues`, data)
      .pipe(
        tap((createdQueue) => {
          this.queues.update((queues) => [createdQueue, ...queues]);
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to create queue');
          throw err;
        })
      );
  }

  /**
   * GET /api/projects/{projectId}/queues/{queueId}
   */
  getQueue(projectId: string, queueId: string): Observable<JobQueue> {
    return this.http
      .get<JobQueue>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/queues/${encodeURIComponent(queueId)}`
      )
      .pipe(
        catchError((err) => {
          this.error.set(err.message || 'Failed to fetch queue');
          throw err;
        })
      );
  }

  /**
   * PATCH /api/projects/{projectId}/queues/{queueId}
   */
  updateQueue(
    projectId: string,
    queueId: string,
    data: JobQueueUpdateRequest
  ): Observable<JobQueue> {
    return this.http
      .patch<JobQueue>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/queues/${encodeURIComponent(queueId)}`,
        data
      )
      .pipe(
        tap((updatedQueue) => {
          this.queues.update((queues) =>
            queues.map((queue) => (queue.queue_id === queueId ? updatedQueue : queue))
          );
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to update queue');
          throw err;
        })
      );
  }

  /**
   * DELETE /api/projects/{projectId}/queues/{queueId}
   */
  deleteQueue(projectId: string, queueId: string): Observable<{ deleted: boolean }> {
    return this.http
      .delete<{ deleted: boolean }>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/queues/${encodeURIComponent(queueId)}`
      )
      .pipe(
        tap(() => {
          this.queues.update((queues) => queues.filter((queue) => queue.queue_id !== queueId));
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to delete queue');
          throw err;
        })
      );
  }

  /**
   * POST /api/projects/{projectId}/queues/{queueId}/start
   */
  startQueue(projectId: string, queueId: string): Observable<JobQueue> {
    return this.http
      .post<JobQueue>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/queues/${encodeURIComponent(queueId)}/start`,
        {}
      )
      .pipe(
        tap((updatedQueue) => {
          this.queues.update((queues) =>
            queues.map((queue) => (queue.queue_id === queueId ? updatedQueue : queue))
          );
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to start queue');
          throw err;
        })
      );
  }

  /**
   * POST /api/projects/{projectId}/queues/{queueId}/stop
   */
  stopQueue(projectId: string, queueId: string): Observable<JobQueue> {
    return this.http
      .post<JobQueue>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/queues/${encodeURIComponent(queueId)}/stop`,
        {}
      )
      .pipe(
        tap((updatedQueue) => {
          this.queues.update((queues) =>
            queues.map((queue) => (queue.queue_id === queueId ? updatedQueue : queue))
          );
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to stop queue');
          throw err;
        })
      );
  }

  /**
   * Helper to refresh queues list
   */
  refreshQueues(projectId: string): void {
    this.loading.set(true);
    this.listQueues(projectId).subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  /**
   * Helper to clear error
   */
  clearError(): void {
    this.error.set(null);
  }
}
