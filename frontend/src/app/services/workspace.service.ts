import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of } from 'rxjs';
import { FileTreeResponse, FileContentResponse, GitDiffResponse, FileTreeNode } from '../models/workspace.model';

@Injectable({ providedIn: 'root' })
export class WorkspaceService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/workspace';

  // Track current project ID for SSE auto-refresh callbacks
  private _currentProjectId: string | null = null;

  // State signals
  readonly currentTree = signal<FileTreeNode[] | null>(null);
  readonly currentFile = signal<FileContentResponse | null>(null);
  readonly currentDiff = signal<GitDiffResponse | null>(null);
  readonly selectedPath = signal<string | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /** GET /api/workspace/{projectId}/tree */
  getFileTree(projectId: string, path: string = '.'): Observable<FileTreeResponse> {
    this._currentProjectId = projectId;
    const params = new HttpParams().set('path', path);
    return this.http.get<FileTreeResponse>(
      `${this.API_BASE}/${encodeURIComponent(projectId)}/tree`, { params }
    ).pipe(
      tap(res => this.currentTree.set(res.tree)),
      catchError(err => {
        this.error.set(err.message || 'Failed to load file tree');
        return of({ project_id: projectId, path, tree: [], truncated: false });
      })
    );
  }

  /** GET /api/workspace/{projectId}/file */
  getFileContent(projectId: string, path: string): Observable<FileContentResponse> {
    const params = new HttpParams().set('path', path);
    return this.http.get<FileContentResponse>(
      `${this.API_BASE}/${encodeURIComponent(projectId)}/file`, { params }
    ).pipe(
      tap(res => {
        this.currentFile.set(res);
        this.selectedPath.set(path);
      }),
      catchError(err => {
        this.error.set(err.message || 'Failed to read file');
        throw err;
      })
    );
  }

  /** GET /api/workspace/{projectId}/diff */
  getFileDiff(projectId: string, path: string): Observable<GitDiffResponse> {
    const params = new HttpParams().set('path', path);
    return this.http.get<GitDiffResponse>(
      `${this.API_BASE}/${encodeURIComponent(projectId)}/diff`, { params }
    ).pipe(
      tap(res => this.currentDiff.set(res)),
      catchError(err => {
        this.error.set(err.message || 'Failed to get diff');
        throw err;
      })
    );
  }

  /** Expand a directory node lazily */
  expandDirectory(projectId: string, node: FileTreeNode): Observable<FileTreeResponse> {
    return this.getFileTree(projectId, node.path);
  }

  clearError(): void {
    this.error.set(null);
  }
}
