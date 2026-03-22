import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, map, of } from 'rxjs';
import {
  Schedule,
  ScheduleCreateRequest,
  ScheduleUpdateRequest,
  ScheduleListResponse,
  ExecutionListResponse,
  ScheduleConfiguration,
  ScheduleExecution,
} from '../models/scheduler.model';

export interface TriggerResponse {
  execution_id: string;
}

export interface ValidationResponse {
  valid: boolean;
  error?: string;
}

@Injectable({
  providedIn: 'root'
})
export class SchedulerService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/schedules';

  // Signals for state
  readonly schedules = signal<Schedule[]>([]);
  readonly executions = signal<ScheduleExecution[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/schedules
   */
  listSchedules(): Observable<Schedule[]> {
    return this.http.get<ScheduleListResponse>(this.API_BASE).pipe(
      tap((response) => this.schedules.set(response.schedules)),
      map((response) => response.schedules),
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch schedules');
        return of([] as Schedule[]);
      })
    );
  }

  /**
   * GET /api/schedules/{id}
   */
  getSchedule(id: string): Observable<Schedule> {
    return this.http.get<Schedule>(`${this.API_BASE}/${encodeURIComponent(id)}`).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch schedule');
        throw err;
      })
    );
  }

  /**
   * POST /api/schedules
   */
  createSchedule(request: ScheduleCreateRequest): Observable<Schedule> {
    return this.http.post<Schedule>(this.API_BASE, request).pipe(
      tap((schedule) => {
        this.schedules.update((schedules) => [schedule, ...schedules]);
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to create schedule');
        throw err;
      })
    );
  }

  /**
   * PUT /api/schedules/{id}
   */
  updateSchedule(id: string, request: ScheduleUpdateRequest): Observable<Schedule> {
    return this.http.put<Schedule>(`${this.API_BASE}/${encodeURIComponent(id)}`, request).pipe(
      tap((updatedSchedule) => {
        this.schedules.update((schedules) =>
          schedules.map((s) => (s.id === id ? updatedSchedule : s))
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to update schedule');
        throw err;
      })
    );
  }

  /**
   * DELETE /api/schedules/{id}
   */
  deleteSchedule(id: string): Observable<void> {
    return this.http.delete<void>(`${this.API_BASE}/${encodeURIComponent(id)}`).pipe(
      tap(() => {
        this.schedules.update((schedules) => schedules.filter((s) => s.id !== id));
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to delete schedule');
        throw err;
      })
    );
  }

  /**
   * POST /api/schedules/{id}/start
   */
  startSchedule(id: string): Observable<Schedule> {
    return this.http.post<Schedule>(`${this.API_BASE}/${encodeURIComponent(id)}/start`, {}).pipe(
      tap((schedule) => {
        this.schedules.update((schedules) =>
          schedules.map((s) => (s.id === id ? schedule : s))
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to start schedule');
        throw err;
      })
    );
  }

  /**
   * POST /api/schedules/{id}/stop
   */
  stopSchedule(id: string): Observable<Schedule> {
    return this.http.post<Schedule>(`${this.API_BASE}/${encodeURIComponent(id)}/stop`, {}).pipe(
      tap((schedule) => {
        this.schedules.update((schedules) =>
          schedules.map((s) => (s.id === id ? schedule : s))
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to stop schedule');
        throw err;
      })
    );
  }

  /**
   * POST /api/schedules/{id}/trigger
   */
  triggerSchedule(id: string): Observable<TriggerResponse> {
    return this.http.post<TriggerResponse>(
      `${this.API_BASE}/${encodeURIComponent(id)}/trigger`,
      {}
    ).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to trigger schedule');
        throw err;
      })
    );
  }

  /**
   * GET /api/schedules/{scheduleId}/executions
   */
  getExecutions(scheduleId: string, limit?: number, offset?: number): Observable<ScheduleExecution[]> {
    let params = new HttpParams();
    if (limit !== undefined) params = params.set('limit', limit.toString());
    if (offset !== undefined) params = params.set('offset', offset.toString());

    return this.http.get<ExecutionListResponse>(
      `${this.API_BASE}/${encodeURIComponent(scheduleId)}/executions`,
      { params }
    ).pipe(
      tap((response) => this.executions.set(response.executions)),
      map((response) => response.executions),
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch executions');
        return of([] as ScheduleExecution[]);
      })
    );
  }

  /**
   * POST /api/schedules/validate
   */
  validateSchedule(config: ScheduleConfiguration): Observable<ValidationResponse> {
    return this.http.post<ValidationResponse>(`${this.API_BASE}/validate`, config).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to validate schedule');
        throw err;
      })
    );
  }

  /**
   * Helper to refresh schedules list
   */
  refreshSchedules(): void {
    this.loading.set(true);
    this.listSchedules().subscribe({
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
