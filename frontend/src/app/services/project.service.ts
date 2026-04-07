import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap, catchError, of } from 'rxjs';
import { Project, ProjectListResponse } from '../models/project.model';

@Injectable({
  providedIn: 'root'
})
export class ProjectService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/projects';

  // Signals for state
  readonly projects = signal<Project[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /**
   * GET /api/projects
   */
  listProjects(): Observable<ProjectListResponse> {
    return this.http.get<ProjectListResponse>(this.API_BASE).pipe(
      tap((response) => this.projects.set(response.projects)),
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch projects');
        return of({ projects: [], total: 0 });
      })
    );
  }

  /**
   * GET /api/projects/{id}
   */
  getProject(projectId: string): Observable<Project> {
    return this.http.get<Project>(`${this.API_BASE}/${encodeURIComponent(projectId)}`).pipe(
      catchError((err) => {
        this.error.set(err.message || 'Failed to fetch project');
        throw err;
      })
    );
  }

  /**
   * POST /api/projects/{id}/pause-queue
   */
  pauseJobQueue(projectId: string): Observable<Project> {
    return this.http.post<Project>(`${this.API_BASE}/${encodeURIComponent(projectId)}/pause-queue`, {}).pipe(
      tap((updatedProject) => {
        this.projects.update((projects) =>
          projects.map((project) =>
            project.project_id === projectId ? updatedProject : project
          )
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to pause job queue');
        throw err;
      })
    );
  }

  /**
   * POST /api/projects/{id}/resume-queue
   */
  resumeJobQueue(projectId: string): Observable<Project> {
    return this.http.post<Project>(`${this.API_BASE}/${encodeURIComponent(projectId)}/resume-queue`, {}).pipe(
      tap((updatedProject) => {
        this.projects.update((projects) =>
          projects.map((project) =>
            project.project_id === projectId ? updatedProject : project
          )
        );
      }),
      catchError((err) => {
        this.error.set(err.message || 'Failed to resume job queue');
        throw err;
      })
    );
  }

  /**
   * Helper to refresh projects list
   */
  refreshProjects(): void {
    this.loading.set(true);
    this.listProjects().subscribe({
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
