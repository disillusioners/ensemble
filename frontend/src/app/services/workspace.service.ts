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

  /**
   * Project ID that the `currentTree` signal's data belongs to. Updated
   * whenever `currentTree` is populated (by `getFileTree`'s tap or by
   * `restoreState`) and cleared by `resetState`.
   *
   * Used by `saveCurrentState` to detect cross-project races: during a
   * project switch the live `currentTree`/`selectedPath` signals may
   * still hold the OUTGOING project's data (or a late HTTP response from
   * the outgoing project may have repopulated them after the switch).
   * Snapshotting those signals blindly under `projectId` would corrupt
   * the cache — A's tree would be cached under B and vice versa.
   * Guarding on `_treeProjectId === projectId` prevents that.
   */
  private _treeProjectId: string | null = null;

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
        tap((res) => {
          this.currentTree.set(res.tree);
          // Tag the tree data with the project that initiated this
          // request. The response may arrive AFTER the user has switched
          // projects (e.g. an SSE-triggered refetch or a slow network),
          // so `_treeProjectId` here intentionally reflects the
          // REQUESTING project, not whatever `_currentProjectId` is now.
          // `saveCurrentState` uses this tag to refuse snapshotting
          // stale data under the wrong projectId.
          this._treeProjectId = projectId;
        }),
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

  /**
   * PUT /api/workspace/{projectId}/file
   *
   * Intentionally side-effect free on the service's own signals:
   *   - We do NOT mutate `currentFile` here. F2 — the previous
   *     implementation re-broadcast the saved content through
   *     `currentFile`, which retriggered the CodeViewerComponent's
   *     `currentFile` effect and reset `editedContent`, clobbering
   *     any keystrokes the user typed while the PUT was in flight.
   *   - We do NOT set `this.error` in catchError. F8 — the
   *     WorkspaceComponent shows the failure via a status-mapped
   *     MatSnackBar (single error presentation). Writing here too
   *     produced a double-banner UX.
   *
   * Dirty-state management is owned by the consumer: after the PUT
   * resolves, the component calls `codeViewer.markSaved()` to align
   * the saved-state baseline with the content that was just written.
   */
  saveFile(projectId: string, path: string, content: string): Observable<FileWriteResponse> {
    const body: FileWriteRequest = { path, content };
    return this.http
      .put<FileWriteResponse>(
        `${this.API_BASE}/${encodeURIComponent(projectId)}/file`,
        body
      )
      .pipe(
        catchError((err) => {
          // Rethrow unchanged so the component's snackbar handler can
          // map status → message. Deliberately NOT touching `this.error`
          // — single presentation per F8.
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
    this._treeProjectId = null;
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

    // Only snapshot the live service signals when they actually belong to
    // `projectId`. During a switch the live signals may still hold the
    // outgoing project's data — and a late HTTP response from that
    // outgoing project can repopulate them AFTER the switch has already
    // updated `_currentProjectId`. Snapshotting blindly would cache the
    // wrong tree under the wrong projectId (e.g. project B's tree
    // getting cached under project A), corrupting the cache so a later
    // `restoreState(A)` returns B's tree.
    //
    // When the live signals don't belong to this project, fall back to
    // the prior cached entry so we don't wipe a good cached value with
    // null just because we happened to be mid-switch.
    //
    // `_treeProjectId === null` is treated as "no tree loaded yet" — in
    // that state `currentTree` was cleared by `resetState` so there is no
    // tree to corrupt, and the caller is explicitly seeding the cache with
    // live signal values (e.g. setting `selectedPath` before a manual
    // `saveCurrentState`). This preserves the pre-fix behavior for the
    // no-tree case while still blocking the cross-project race.
    const liveSignalsBelongToProject =
      this._treeProjectId === null || this._treeProjectId === projectId;

    const state: WorkspaceState = {
      projectId,
      tree: liveSignalsBelongToProject
        ? this.currentTree()
        : prior?.tree ?? null,
      selectedPath: liveSignalsBelongToProject
        ? this.selectedPath()
        : prior?.selectedPath ?? null,
      viewMode: uiExtras.viewMode ?? prior?.viewMode ?? 'code',
      // Explicit `[]` means "all directories collapsed" and is a valid
      // caller-provided value — `??` intentionally preserves it rather
      // than falling back to `prior`, so collapsing the tree on
      // project A and switching to B correctly clears A's expandedPaths.
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
   * When switching projects, snapshots the outgoing live signals before
   * applying the cached state and updates the current project ID so subsequent
   * SSE connection setup cannot cache the restored signals under the old ID.
   *
   * `outgoingUiExtras` are passed to the internal `saveCurrentState` call
   * for the outgoing project. The component owns UI state that lives
   * outside the service (e.g. FileTreeComponent's expanded paths and the
   * component-owned viewMode signal), so it must capture them BEFORE the
   * switch and hand them in here — the service has no reference to those
   * components. Without this, the outgoing snapshot always has empty
   * `expandedPaths` and the default `viewMode`, so a subsequent switch
   * back loses the user's tree-expansion and view-mode state.
   */
  restoreState(
    projectId: string,
    outgoingUiExtras: WorkspaceUiExtras = {}
  ): WorkspaceState | null {
    const state = this._stateCache.get(projectId);
    if (!state) return null;

    // The caller (typically WorkspaceComponent) owns UI state that lives
    // outside the service — FileTreeComponent's expanded paths and the
    // component-owned viewMode signal. Those must be saved against the
    // ACTIVE/OUTGOING project BEFORE we reset the live signals and
    // apply the incoming project's cached state, otherwise the outgoing
    // snapshot only carries the service's own signals (empty expanded
    // paths, default viewMode) and a return switch loses the user's UI
    // state. `saveCurrentState` safely ignores an empty projectId, so
    // passing `''` on the very first restore (no active project yet)
    // is a no-op rather than an error.
    const outgoingProjectId = this._currentProjectId ?? '';
    this.saveCurrentState(outgoingProjectId, outgoingUiExtras);

    this.resetState();
    this._currentProjectId = projectId;

    // Promote to MRU by re-inserting.
    this._stateCache.delete(projectId);
    this._stateCache.set(projectId, state);

    // Apply service-owned fields to live signals.
    this.currentTree.set(state.tree);
    // Tag the restored tree with the project it belongs to so a
    // subsequent `saveCurrentState` for THIS project will snapshot it.
    // Even if `state.tree` is null (empty project / never loaded) we
    // still tag ownership — we're now displaying this project's state.
    this._treeProjectId = projectId;
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
   * Side-effect-free read of the cached snapshot for `projectId`.
   * Returns the stored `WorkspaceState` without mutating any signal,
   * applying state, or reordering the underlying Map / LRU recency
   * list. Intended for assertions and diagnostics; production code
   * should use `restoreState` so the cache entry is promoted to MRU
   * and the live signals are updated.
   */
  peekCachedState(projectId: string): WorkspaceState | null {
    return this._stateCache.get(projectId) ?? null;
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