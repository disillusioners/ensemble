import { Injectable, NgZone, OnDestroy, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, catchError, finalize, of, tap } from 'rxjs';
import {
  FileChangeEvent,
  FileContentResponse,
  FileTreeNode,
  FileTreeResponse,
  GitDiffResponse,
} from '../models/workspace.model';

@Injectable({ providedIn: 'root' })
export class WorkspaceService implements OnDestroy {
  private readonly http = inject(HttpClient);
  private readonly ngZone = inject(NgZone);
  private readonly API_BASE = '/api/workspace';
  private eventSource: EventSource | null = null;

  // Track current project ID for SSE auto-refresh callbacks
  private _currentProjectId: string | null = null;

  // State signals
  readonly sseConnected = signal(false);
  readonly fileChanged = signal<FileChangeEvent | null>(null);
  readonly currentTree = signal<FileTreeNode[] | null>(null);
  readonly currentFile = signal<FileContentResponse | null>(null);
  readonly currentDiff = signal<GitDiffResponse | null>(null);
  readonly selectedPath = signal<string | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /** GET /api/workspace/{projectId}/tree */
  getFileTree(projectId: string, path: string = '.'): Observable<FileTreeResponse> {
    if (this._currentProjectId && this._currentProjectId !== projectId) {
      this.resetState();
    }
    this.loading.set(true);
    this._currentProjectId = projectId;
    const params = new HttpParams().set('path', path);
    return this.http
      .get<FileTreeResponse>(`${this.API_BASE}/${encodeURIComponent(projectId)}/tree`, { params })
      .pipe(
        tap((res) => this.currentTree.set(res.tree)),
        catchError((err) => {
          this.error.set(err.message || 'Failed to load file tree');
          return of({ project_id: projectId, path, tree: [], truncated: false });
        }),
        finalize(() => this.loading.set(false))
      );
  }

  /** GET /api/workspace/{projectId}/file */
  getFileContent(projectId: string, path: string): Observable<FileContentResponse> {
    const params = new HttpParams().set('path', path);
    return this.http
      .get<FileContentResponse>(`${this.API_BASE}/${encodeURIComponent(projectId)}/file`, { params })
      .pipe(
        tap((res) => {
          this.currentFile.set(res);
          this.selectedPath.set(path);
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to read file');
          throw err;
        })
      );
  }

  /** GET /api/workspace/{projectId}/diff */
  getFileDiff(projectId: string, path: string): Observable<GitDiffResponse> {
    const params = new HttpParams().set('path', path);
    return this.http
      .get<GitDiffResponse>(`${this.API_BASE}/${encodeURIComponent(projectId)}/diff`, { params })
      .pipe(
        tap((res) => this.currentDiff.set(res)),
        catchError((err) => {
          this.error.set(err.message || 'Failed to get diff');
          throw err;
        })
      );
  }

  /** Expand a directory node lazily */
  expandDirectory(projectId: string, node: FileTreeNode): Observable<FileTreeResponse> {
    return this.getFileTree(projectId, node.path);
  }

  connectSSE(projectId: string): void {
    this.disconnectSSE();
    if (this._currentProjectId && this._currentProjectId !== projectId) {
      this.resetState();
    }
    this._currentProjectId = projectId;
    let source: EventSource;
    try {
      source = new EventSource(`${this.API_BASE}/${encodeURIComponent(projectId)}/events`);
    } catch (err) {
      console.error(
        '[WorkspaceService] EventSource ctor failed (likely no EventSource in test env):',
        err
      );
      this.sseConnected.set(false);
      return;
    }
    this.eventSource = source;

    source.addEventListener('connected', () => {
      this.ngZone.run(() => this.sseConnected.set(true));
    });

    source.addEventListener('file_changed', (event: MessageEvent) => {
      this.ngZone.run(() => {
        try {
          const data = JSON.parse(event.data) as {
            path: string;
            change_type: string;
            timestamp?: number;
          };
          if (!data || typeof data.path !== 'string') return;
          this.fileChanged.set({
            path: data.path,
            type: data.change_type,
            timestamp: data.timestamp,
          });
          this.handleFileChange(data.path);
        } catch (err) {
          console.error('[WorkspaceService] Failed to parse file_changed:', err);
        }
      });
    });

    source.addEventListener('keepalive', () => {
      this.ngZone.run(() => {
        // Connection is alive; no-op.
      });
    });

    source.onerror = () => {
      this.ngZone.run(() => this.sseConnected.set(false));
      // EventSource auto-reconnects natively; do not close on transient errors.
    };
  }

  disconnectSSE(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.sseConnected.set(false);
  }

  clearError(): void {
    this.error.set(null);
  }

  /**
   * Clear all project-scoped state. Call this when the user switches to a
   * different project so the previous project's tree/file/selection does not
   * flash briefly while the new project's data is loading.
   */
  resetState(): void {
    this.currentTree.set(null);
    this.currentFile.set(null);
    this.selectedPath.set(null);
    this.currentDiff.set(null);
    this.error.set(null);
  }

  ngOnDestroy(): void {
    this.disconnectSSE();
  }

  private handleFileChange(changedPath: string): void {
    const selected = this.selectedPath();
    if (selected && selected === changedPath) {
      const projectId = this._currentProjectId;
      if (!projectId) return;
      this.getFileContent(projectId, changedPath).subscribe({
        error: () => undefined,
      });
    }
  }
}
