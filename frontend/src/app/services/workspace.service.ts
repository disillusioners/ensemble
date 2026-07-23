import { Injectable, NgZone, OnDestroy, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, catchError, finalize, of, tap } from 'rxjs';
import {
  FileChangeEvent,
  FileContentResponse,
  FileTreeNode,
  FileTreeResponse,
  FileWriteRequest,
  FileWriteResponse,
  GitDiffResponse,
} from '../models/workspace.model';

/** Maximum number of projects kept in the LRU state cache. */
const MAX_CACHED_PROJECTS = 5;

/**
 * Serializable snapshot of per-project workspace state. Used to preserve
 * file tree, selection, view mode, expanded tree paths and scroll position
 * across project switches. Plain data only — no Angular signals or
 * non-serializable refs — so it can be safely stored in an LRU cache and
 * round-tripped if persistence is added later.
 */
export interface WorkspaceState {
  /** Project identifier the state belongs to. */
  projectId: string;
  /** Last-loaded file tree (or null if nothing was loaded yet). */
  tree: FileTreeNode[] | null;
  /** Currently selected file path (or null). */
  selectedPath: string | null;
  /** Active view mode. */
  viewMode: 'code' | 'diff';
  /** Expanded tree-node paths captured from the FileTreeComponent. */
  expandedPaths: string[];
  /** ScrollTop of the main viewer (0 when untracked). */
  scrollTop: number;
  /** Timestamp of the snapshot, useful for debugging / eviction policy. */
  capturedAt: number;
}

/**
 * Caller-supplied UI fields that live outside the service's own signals
 * (e.g. viewMode owned by the component, expanded paths owned by the
 * FileTreeComponent). Pass these in at save-time so the cache contains a
 * complete snapshot, and read them off the returned WorkspaceState at
 * restore-time.
 */
export type WorkspaceUiExtras = Partial<
  Pick<WorkspaceState, 'viewMode' | 'expandedPaths' | 'scrollTop'>
>;

@Injectable({ providedIn: 'root' })
export class WorkspaceService implements OnDestroy {
  private readonly http = inject(HttpClient);
  private readonly ngZone = inject(NgZone);
  private readonly API_BASE = '/api/workspace';
  private eventSource: EventSource | null = null;

  // Track current project ID for SSE auto-refresh callbacks
  private _currentProjectId: string | null = null;

  // ── LRU state cache ───────────────────────────────────────────────
  // Map preserves insertion order in JS, which we use as the LRU recency
  // list: most-recently-used entries are re-inserted at the tail; the
  // head is always the least-recently-used candidate for eviction.
  private readonly _stateCache = new Map<string, WorkspaceState>();

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
      // Save outgoing project's state before resetting signals. The caller
      // is expected to have provided extras via saveCurrentState() before
      // triggering the switch — this branch only snapshots the service's
      // own signals. Extras should already be cached by then.
      this.saveCurrentState(this._currentProjectId);
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

  /** PUT /api/workspace/{projectId}/file */
  saveFile(projectId: string, path: string, content: string): Observable<FileWriteResponse> {
    const body: FileWriteRequest = { path, content };
    return this.http
      .put<FileWriteResponse>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/file`,
        body
      )
      .pipe(
        tap((res) => {
          // If the saved file is the currently selected one, refresh its
          // in-memory content + size so the editor reflects the write
          // without needing a round-trip to the server.
          const current = this.currentFile();
          if (current && current.path === path) {
            this.currentFile.set({
              ...current,
              content,
              size_bytes: res.size_bytes,
            });
          }
        }),
        catchError((err) => {
          this.error.set(err.message || 'Failed to save file');
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
      this.saveCurrentState(this._currentProjectId);
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

  // ── LRU cache API ──────────────────────────────────────────────────

  /**
   * Snapshot the current service signals into the LRU cache for
   * `projectId`, optionally merging in caller-supplied UI fields (view
   * mode, expanded paths, scroll position). The entry is marked
   * most-recently-used; if the cache is over capacity, the
   * least-recently-used entry is evicted.
   *
   * No-op when `projectId` is empty.
   */
  saveCurrentState(projectId: string, uiExtras: WorkspaceUiExtras = {}): void {
    if (!projectId) return;

    // Preserve any prior cached extras so a caller passing only viewMode
    // doesn't accidentally wipe previously-saved expandedPaths.
    const prior = this._stateCache.get(projectId);
    const state: WorkspaceState = {
      projectId,
      tree: this.currentTree(),
      selectedPath: this.selectedPath(),
      viewMode: uiExtras.viewMode ?? prior?.viewMode ?? 'code',
      expandedPaths: uiExtras.expandedPaths ?? prior?.expandedPaths ?? [],
      scrollTop: uiExtras.scrollTop ?? prior?.scrollTop ?? 0,
      capturedAt: Date.now(),
    };

    // Re-insert to mark MRU; Map.set on an existing key re-orders it to
    // the tail in insertion-order iteration, which is exactly what we
    // want for LRU semantics.
    if (this._stateCache.has(projectId)) {
      this._stateCache.delete(projectId);
    }
    this._stateCache.set(projectId, state);

    // Evict LRU (oldest = first iterated) while over capacity.
    while (this._stateCache.size > MAX_CACHED_PROJECTS) {
      const oldestKey = this._stateCache.keys().next().value;
      if (oldestKey === undefined) break;
      this._stateCache.delete(oldestKey);
    }
  }

  /**
   * Return cached state for `projectId` and mark it most-recently-used.
   * Applies the cache to the live service signals (tree, selectedPath)
   * so consumers immediately see the restored data. Returns null when
   * nothing is cached. The returned `WorkspaceState` contains caller-UI
   * fields (viewMode, expandedPaths, scrollTop) that the consuming
   * component is expected to apply to itself / its child components.
   *
   * Does NOT change `_currentProjectId`; callers must do that themselves
   * after deciding how to load the project (cache vs fresh fetch).
   */
  restoreState(projectId: string): WorkspaceState | null {
    const state = this._stateCache.get(projectId);
    if (!state) return null;

    // Promote to MRU by re-inserting.
    this._stateCache.delete(projectId);
    this._stateCache.set(projectId, state);

    // Apply service-owned fields to live signals.
    this.currentTree.set(state.tree);
    this.selectedPath.set(state.selectedPath);
    this.currentFile.set(null);
    this.currentDiff.set(null);

    return state;
  }

  /** True when `projectId` has a cached snapshot. */
  hasCachedState(projectId: string): boolean {
    return this._stateCache.has(projectId);
  }

  /**
   * Clear one project's cached state, or the entire cache when `projectId`
   * is omitted.
   */
  clearCache(projectId?: string): void {
    if (projectId === undefined) {
      this._stateCache.clear();
      return;
    }
    this._stateCache.delete(projectId);
  }

  /** Test/debug accessor — current size of the LRU cache. */
  cacheSize(): number {
    return this._stateCache.size;
  }

  /** Maximum capacity of the LRU cache. Exposed for tests and diagnostics. */
  get maxCachedProjects(): number {
    return MAX_CACHED_PROJECTS;
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