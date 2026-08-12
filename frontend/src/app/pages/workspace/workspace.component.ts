import {
  Component,
  DestroyRef,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
  Output,
  SimpleChanges,
  ViewChild,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeComponent } from '../../components/file-tree/file-tree.component';
import { CodeViewerComponent } from '../../components/code-viewer/code-viewer.component';
import { DiffViewerComponent } from '../../components/diff-viewer/diff-viewer.component';
import { FileTabsComponent } from '../../components/file-tabs/file-tabs.component';
import { VsCodeEditorCacheComponent } from '../../components/vscode-editor-cache/vscode-editor-cache.component';
import {
  ConfirmDialogComponent,
  ConfirmDialogData,
} from '../../components/confirm-dialog/confirm-dialog.component';

/**
 * Workspace viewer.
 *
 * Two usage modes:
 *   1. Standalone page (route `/projects/:projectId/workspace`): the
 *      `projectId` is read from `ActivatedRoute.snapshot.paramMap` and
 *      used to seed initialisation. The deep-link keeps working.
 *   2. Reusable overlay: a host component sets `[projectId]` via the
 *      `@Input()` and listens for `(hide)` to dismiss. When `projectId`
 *      changes, the component restores cached state for that project (if
 *      available) or loads a fresh file tree, and reconnects the SSE
 *      stream.
 *
 * The input takes precedence over the route — useful when both are
 * present (e.g. a parent passes an explicit value while a stale route
 * param is still in scope).
 */
@Component({
  selector: 'app-workspace',
  standalone: true,
  imports: [
    CommonModule,
    MatSidenavModule,
    MatToolbarModule,
    MatButtonToggleModule,
    MatIconModule,
    MatButtonModule,
    MatTooltipModule,
    MatSnackBarModule,
    MatDialogModule,
    MatProgressSpinnerModule,
    FileTreeComponent,
    CodeViewerComponent,
    DiffViewerComponent,
    FileTabsComponent,
    VsCodeEditorCacheComponent,
  ],
  template: `
    <div class="workspace-container">
      <mat-sidenav-container class="sidenav-container">
        @if (editorMode() === 'builtin') {
          <mat-sidenav mode="side" opened class="file-tree-sidenav">
            <div class="tree-header">
              <mat-icon>folder_open</mat-icon>
              <span>Files</span>
            </div>
            <app-file-tree
              [projectId]="projectId"
              [openPaths]="workspace.openFiles().map(f => f.path)"
              [activePath]="workspace.activeFilePath()"
              (fileSelected)="onFileSelected($event)"
            ></app-file-tree>
          </mat-sidenav>
        }

        <mat-sidenav-content class="content-area">
          @if (editorMode() === 'builtin') {
            <mat-toolbar class="content-toolbar">
              <span class="toolbar-title">
                {{ selectedPath() || 'Select a file' }}
                @if (isCodeViewerDirty()) {
                  <span
                    class="dirty-indicator"
                    data-testid="dirty-indicator"
                    aria-label="Unsaved changes"
                    title="Unsaved changes"
                  >*</span>
                }
              </span>
              @if (currentFile()) {
                <span class="file-meta">{{ currentFile()?.total_lines }} lines · {{ formatSize(currentFile()?.size_bytes) }}</span>
                @if (currentFile()?.binary) { <span class="badge badge-binary">BIN</span> }
                @if (currentFile()?.truncated) { <span class="badge badge-truncated">TRUNC</span> }
              }
              <span class="spacer"></span>
              @if (selectedPath()) {
                <mat-button-toggle-group [value]="viewMode()">
                  <mat-button-toggle value="code" (change)="onSelectCode()">
                    <mat-icon>code</mat-icon> Code
                  </mat-button-toggle>
                  <mat-button-toggle value="diff" (change)="onSelectDiff()">
                    <mat-icon>compare_arrows</mat-icon> Diff
                  </mat-button-toggle>
                </mat-button-toggle-group>
              }
              @if (selectedPath()) {
                <button mat-icon-button data-testid="save-button" aria-label="Save" [disabled]="!canSave()" (click)="saveFile()" matTooltip="Save (Ctrl+S)">
                  @if (saving()) { <mat-icon class="spin">hourglass_empty</mat-icon> }
                  @else { <mat-icon>save</mat-icon> }
                </button>
              }
              <span
                class="sse-indicator"
                [class.sse-connected]="workspace.sseConnected()"
                [attr.aria-label]="workspace.sseConnected() ? 'Live' : 'Disconnected'"
              >
                <span class="sse-dot"></span>
                <span class="sse-label">{{ workspace.sseConnected() ? 'Live' : 'Disconnected' }}</span>
              </span>
              <button
                mat-icon-button
                type="button"
                class="hide-button"
                aria-label="Hide workspace"
                data-testid="workspace-hide"
                (click)="onHide()"
              >
                <mat-icon>visibility_off</mat-icon>
              </button>
            </mat-toolbar>

            <app-file-tabs
              [openFiles]="workspace.openFiles()"
              [activePath]="workspace.activeFilePath()"
              (tabClick)="onTabClick($event)"
              (closeTab)="onTabClose($event)"
            />
          }

          <div class="viewer-content">
            @if (workspace.error(); as errorMessage) {
              <div class="error-banner" role="alert">
                <mat-icon class="error-icon">error_outline</mat-icon>
                <span class="error-message">{{ errorMessage }}</span>
                <span class="spacer"></span>
                <button mat-icon-button aria-label="Dismiss error" (click)="workspace.clearError()">
                  <mat-icon>close</mat-icon>
                </button>
              </div>
            }
            @switch (editorMode()) {
              @case ('builtin') {
                @if (viewMode() === 'code') {
                  @if (workspace.activeFilePath()) {
                    <app-code-viewer></app-code-viewer>
                  } @else {
                    <div class="empty-state" data-testid="workspace-empty-state">
                      <mat-icon>description</mat-icon>
                      <span>Select a file from the tree to view</span>
                    </div>
                  }
                } @else {
                  <app-diff-viewer></app-diff-viewer>
                }
              }
              @case ('vscode') {
                <div class="vscode-overlay-container">
                  <button
                    class="vscode-overlay-hide"
                    mat-icon-button
                    type="button"
                    (click)="onHide()"
                    matTooltip="Hide workspace"
                    aria-label="Hide workspace"
                    data-testid="vscode-overlay-hide"
                  >
                    <mat-icon>visibility_off</mat-icon>
                  </button>
                  <app-vscode-editor-cache
                    [projectId]="projectId"
                    [workdir]="validatedWorkdir()"
                  ></app-vscode-editor-cache>
                </div>
              }
            }
          </div>
        </mat-sidenav-content>
      </mat-sidenav-container>
      @if (workspace.loading() && !workspace.currentTree()) {
        <div class="loading-overlay">
          <mat-spinner diameter="48"></mat-spinner>
          <span>Loading workspace…</span>
        </div>
      }
    </div>
  `,
  styleUrl: './workspace.component.scss',
})
export class WorkspaceComponent implements OnInit, OnChanges, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  readonly workspace = inject(WorkspaceService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  private readonly http = inject(HttpClient);

  @ViewChild(FileTreeComponent, { static: false }) private fileTree?: FileTreeComponent;
  @ViewChild(CodeViewerComponent) codeViewer?: CodeViewerComponent;

  /**
   * Active project ID. May be set by the host (overlay mode) or read from
   * `ActivatedRoute` as a fallback. Empty string means "no project yet".
   *
   * Implemented as a setter so input changes — whether from template
   * bindings, host updates, or tests — reliably trigger the load logic
   * without depending on Angular's ngOnChanges timing.
   */
  private _projectId = '';
  @Input()
  public get projectId(): string {
    return this._projectId;
  }
  public set projectId(value: string) {
    const next = value ?? '';
    const previous = this._projectId;
    this._projectId = next;
    // Only reload when the project actually changes. Initial assignment
    // to the default ('') on construction must not trigger a load —
    // ngOnInit handles that case after the route fallback is applied.
    if (next !== previous && this._initialised && next !== '') {
      // Pass the prior ID so `loadProject` can snapshot the outgoing
      // project's component-owned UI state (FileTree expansions,
      // viewMode) before the incoming project's data replaces it. The
      // initial ngOnInit call passes no previous id, so the very first
      // load never tries to save outgoing state.
      this.loadProject(next, previous);
    }
  }

  /**
   * Emitted when the user clicks the Hide button. Hosts using the
   * component as an overlay should listen for this and dismiss the
   * workspace view.
   */
  @Output() public readonly hide = new EventEmitter<void>();

  /**
   * Whether the workspace is currently visible to the user.
   *
   * The default is `true` so the standalone route page
   * (`/projects/:projectId/workspace`) — which does not bind
   * `[visible]` — keeps rendering normally. Overlay hosts that keep
   * the component always-mounted (so the VS Code editor cache and
   * other expensive subtrees survive a hide/show cycle) MUST bind
   * `[visible]` to their visibility state.
   *
   * When `false`:
   *   - The host should also apply `display: none` via a
   *     `[style.display]` binding (the parent template does this).
   *   - SSE is disconnected (saves a long-lived connection and the
   *     associated HTTP polls for changes the user cannot see).
   *   - Window keyboard handlers are no-ops (Ctrl+S / Escape must
   *     not fire while the workspace is invisible, otherwise the
   *     wrong instance could be saved or the overlay dismissed
   *     while the user is doing something else).
   *
   * Project data still loads when the user switches projects while
   * the workspace is hidden, so re-showing the overlay is instant.
   * SSE is re-connected when visibility flips back to `true`.
   */
  @Input() public visible: boolean = true;

  // `selectedPath` and `currentFile` are computed signals on the
  // service (derived from `_activeFilePath` and the per-path content
  // cache). Computed signals are already readonly, so we re-expose them
  // directly — calling `.asReadonly()` on them is a type error since
  // `Signal<T>` does not have that method.
  public readonly selectedPath = this.workspace.selectedPath;
  public readonly currentFile = this.workspace.currentFile;
  public readonly viewMode = signal<'code' | 'diff'>('code');

  /**
   * Re-export the editor-mode signal from the workspace service so the
   * template can read it without going through the service. The signal
   * is hydrated from `/api/settings/editor` in the service constructor.
   */
  public readonly editorMode = this.workspace.editorMode;

  /**
   * C2 — folder path returned by the dedicated
   * `/api/projects/{id}/vscode-folder` endpoint. The endpoint enforces
   * path containment server-side; we never read
   * `project.main_directory` directly because the user-controlled
   * project registry cannot be trusted to keep that path safe to
   * mount inside code-server.
   */
  public readonly validatedWorkdir = signal<string>('');

  /**
   * F7 — in-flight save guard. Set true at the start of `saveFile()`,
   * cleared in a `finalize` callback so both success and error paths
   * reset the flag. `canSave()` consults this to disable the Save button
   * and block Ctrl+S spam from firing concurrent PUTs.
   */
  public readonly saving = signal(false);

  /**
   * Flag distinguishing "the setter has run with a real (non-default)
   * value at least once" from "we're still in ngOnInit waiting to
   * resolve the route fallback". The setter defers to ngOnInit until
   * `_initialised` is true so the initial route fallback isn't raced
   * by an empty input assignment.
   */
  private _initialised = false;

  /**
   * Captured expansion paths from the FileTreeComponent at the moment we
   * leave builtin mode. Restored when (and if) the user comes back. We
   * capture on the builtin -> vscode transition so the values reflect
   * whatever the user had expanded in builtin mode just before the
   * @if tore down the FileTreeComponent.
   */
  private _savedExpandedPaths: string[] = [];

  constructor() {
    let prevMode: 'builtin' | 'vscode' = this.editorMode();
    effect(() => {
      const currentMode = this.editorMode();
      const prev = prevMode;
      prevMode = currentMode;
      // Skip the initial run (prev === current); only act on transitions.
      if (prev === currentMode) return;

      if (prev === 'builtin' && currentMode === 'vscode') {
        // Switching FROM builtin TO vscode: capture expanded paths BEFORE the
        // @if destroys the <app-file-tree>. `fileTree` may be undefined if
        // static-false resolution hasn't fired yet (it WILL be defined here
        // because we're transitioning AWAY from the builtin branch).
        this._savedExpandedPaths = this.fileTree?.getExpandedPaths() ?? [];
      } else if (prev === 'vscode' && currentMode === 'builtin') {
        // Switching FROM vscode TO builtin: the @if recreates <app-file-tree>.
        // Defer one microtask so @ViewChild has a chance to resolve to the
        // freshly-created child before we touch it. `setTree()` must come
        // before `restoreExpandedPaths()` because setTree clears
        // `_expandedPaths`.
        const paths = this._savedExpandedPaths;
        queueMicrotask(() => {
          if (!this.fileTree) return;
          const tree = this.workspace.currentTree();
          if (tree) {
            this.fileTree.setTree(tree);
          }
          if (paths.length > 0) {
            this.fileTree.restoreExpandedPaths(paths);
          }
        });
      }
    });
  }

  ngOnInit(): void {
    // Fallback: if no input was set, read from the route. The input
    // always wins — useful for overlay hosts that already know the id.
    if (!this._projectId) {
      this._projectId = this.route.snapshot.paramMap.get('projectId') || '';
    }
    this._initialised = true;
    this.loadProject(this._projectId);
  }

  /**
   * React to input changes from the parent template.
   *
   * Only `visible` needs lifecycle handling — `projectId` is wired
   * through a setter (so input changes reliably trigger `loadProject`
   * without depending on `ngOnChanges` timing) and `visible` defaults
   * to `true` for the standalone route, so the only state transition
   * we care about here is a hide/show flip from the parent.
   *
   * On the visible→hidden edge we drop the SSE connection (the
   * user cannot see the workspace, so receiving file-change pushes
   * is wasted work). On the hidden→visible edge we re-establish SSE
   * only when there is a project loaded — the standalone route
   * starts invisible→visible during `ngOnInit`/route resolution, so
   * `_projectId` may be empty and `loadProject` is responsible for
   * the initial connect.
   */
  ngOnChanges(changes: SimpleChanges): void {
    if (changes['visible']) {
      const isVisible = changes['visible'].currentValue as boolean;
      if (isVisible) {
        if (this._projectId) {
          this.workspace.connectSSE(this._projectId);
        }
      } else {
        this.workspace.disconnectSSE();
      }
    }
  }

  ngOnDestroy(): void {
    this.workspace.disconnectSSE();
  }

  /**
   * C2 — fetch the pre-validated folder path for `projectId` from the
   * dedicated endpoint. Never use `project.main_directory` directly:
   * the endpoint enforces path containment server-side so a malicious
   * or misconfigured project cannot escape into `/etc` or another
   * tenant's home directory.
   *
   * No-op when `projectId` is empty. On error, the workdir signal is
   * cleared so the iframe falls back to the base `/vscode/` URL
   * (no folder) rather than opening an unvalidated path.
   */
  loadValidatedWorkdir(projectId: string): void {
    if (!projectId) return;
    this.http
      .get<{ folder: string }>(`/api/projects/${projectId}/vscode-folder`)
      .subscribe({
        next: (resp) => this.validatedWorkdir.set(resp.folder),
        error: () => this.validatedWorkdir.set(''),
      });
  }

  onFileSelected(path: string): void {
    this.viewMode.set('code');
    // `openFile` opens the tab AND fires the `getFileContent` HTTP
    // fetch internally (the fetch populates the per-path content
    // cache, which makes `currentFile` — computed from
    // `_activeFilePath` + cache — update automatically). `openFile`
    // also handles the failure path: on fetch error it closes the
    // tab it just opened so the user doesn't see an empty editor
    // pretending to be a valid open file. We therefore do NOT also
    // call `getFileContent` here — that would fire a duplicate
    // HTTP request.
    this.workspace.openFile(this.projectId, path);
  }

  /**
   * Switch the active tab. Driven by FileTabsComponent's `(tabClick)`
   * output. `setActiveFile` is a no-op when the path is not open, so
   * a stale reference cannot corrupt tab state.
   *
   * `ensureTabContent` populates the per-path content cache when the
   * tab was restored from cache with no content (e.g. after a
   * project switch back to a project that still had this tab open in
   * its LRU snapshot). It is a no-op when the tab is already hydrated
   * or not open, so calling it on every click is safe and avoids
   * duplicate fetches.
   */
  onTabClick(path: string): void {
    this.workspace.setActiveFile(path);
    this.workspace.ensureTabContent(this.projectId, path);
  }

  /**
   * Close a tab. Driven by FileTabsComponent's `(closeTab)` output.
   * The service handles active-tab rebalancing (preferring the right
   * neighbour, then left, then null).
   *
   * If the tab has unsaved edits, the user is prompted with the shared
   * confirmation dialog before the edit state is discarded. `forgetTab`
   * is called on the code viewer's per-path edit-state map so a re-opened
   * tab starts from a clean baseline instead of inheriting stale state.
   */
  onTabClose(path: string): void {
    const tab = this.workspace.openFiles().find((file) => file.path === path);
    if (!tab?.dirty) {
      this.finishTabClose(path);
      return;
    }

    this.dialog
      .open<ConfirmDialogComponent, ConfirmDialogData, boolean>(ConfirmDialogComponent, {
        width: '420px',
        panelClass: 'dark-modal-panel',
        data: {
          title: 'Discard Unsaved Changes',
          message: `"${tab.name}" has unsaved changes. Close and discard them?`,
          confirmLabel: 'Discard',
          cancelLabel: 'Cancel',
          destructive: true,
        },
      })
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((confirmed: boolean | undefined) => {
        if (confirmed) {
          this.finishTabClose(path);
        }
      });
  }

  /** Complete a tab close after any unsaved-change confirmation. */
  private finishTabClose(path: string): void {
    this.codeViewer?.forgetTab(path);
    this.workspace.closeFile(path);
  }

  onSelectCode(): void {
    this.viewMode.set('code');
  }

  onSelectDiff(): void {
    const path = this.selectedPath();
    if (path) {
      this.workspace
        .getFileDiff(this.projectId, path)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({
          next: () => this.viewMode.set('diff'),
          error: () => this.viewMode.set('diff'),
        });
    } else {
      this.viewMode.set('diff');
    }
  }

  onHide(): void {
    this.hide.emit();
  }

  /**
   * Human-readable byte size for the toolbar meta line. Accepts
   * `undefined` / `null` so the template can call it on
   * `currentFile()?.size_bytes` without a guard.
   */
  formatSize(bytes?: number | null): string {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  /**
   * Whether the open file has unsaved edits. Reads the code viewer's
   * computed `isDirty` signal. Returns false when no file is selected,
   * when the view mode is `diff`, or when the code viewer's view child
   * has not yet been resolved.
   */
  isCodeViewerDirty(): boolean {
    return this.viewMode() === 'code' && !!this.codeViewer?.isDirty();
  }

  /**
   * Whether the Save button should be enabled. Save is allowed only
   * when ALL of the following hold:
   *   - No save is currently in flight (F7)
   *   - A file is selected
   *   - The code viewer reports dirty state
   *   - The active view is `code` (not `diff`)
   *   - The current file is editable (F3/F4 — binary and truncated
   *     files are read-only and the editor buffer is intentionally
   *     cleared on entry, so `isDirty()` would already be false;
   *     this is a belt-and-suspenders guard against future changes
   *     to the read-only guard inside the editor.)
   */
  canSave(): boolean {
    if (this.saving()) {
      return false;
    }
    // Read the active path through the tab API so the save flow stays
    // in lock-step with whatever tab the user has focused. `selectedPath`
    // is now a computed derived from `_activeFilePath`, so reading either
    // works — `activeFilePath` is more explicit about intent.
    if (!this.workspace.activeFilePath()) {
      return false;
    }
    if (this.viewMode() !== 'code') {
      return false;
    }
    if (!this.codeViewer?.isDirty()) {
      return false;
    }
    const file = this.codeViewer?.file();
    if (file?.binary || file?.truncated) {
      return false;
    }
    return true;
  }

  /**
   * PUT the current editor content to the workspace backend. Surfaces
   * success and failure via MatSnackBar. No-op when `canSave()` is false
   * (no file selected, view is diff, not dirty, file is read-only, or a
   * save is already in flight).
   *
   * F1/F2 — On success, `codeViewer.markSaved()` aligns the
   * saved-state baseline with the content that was just written so the
   * `savedBaseline`-gated effect allows a same-file SSE push to refresh
   * the editor (which is the round-trip of our own save).
   *
   * F7 — `saving` is set true at entry and cleared in a `finalize`
   * callback so both success and error paths reset the flag.
   *
   * F8 — Single error presentation: the snackbar is the only user-facing
   * surface for save errors. The service deliberately does NOT set its
   * `error` signal in `catchError` so we don't get the double-banner
   * UX. HTTP status codes are mapped to user-friendly messages here.
   */
  saveFile(): void {
    if (!this.canSave()) {
      return;
    }
    const path = this.workspace.activeFilePath();
    if (!path || !this.codeViewer) {
      return;
    }
    // Capture the editor content BEFORE the PUT departs. The same
    // snapshot is sent as the PUT body AND handed to `markSaved` on
    // success, so the saved-state baseline is aligned with what was
    // actually written to disk — not with whatever the editor holds at
    // response time (which may have drifted if the user typed between
    // the PUT departing and the response landing).
    const savedContent = this.codeViewer.currentContent();
    this.saving.set(true);
    this.workspace
      .saveFile(this.projectId, path, savedContent)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.saving.set(false))
      )
      .subscribe({
        next: () => {
          // Align the saved-state baseline so `isDirty()` becomes false
          // and a follow-up SSE push of the same file can refresh the
          // editor (this is the round-trip of our own save). Use the
          // captured `savedContent` — the same string we PUT — so the
          // baseline reflects what landed on disk, not any
          // post-send edits.
          this.codeViewer!.markSaved(savedContent);
          this.snackBar.open('File saved', 'Dismiss', { duration: 3000 });
        },
        error: (err: unknown) => {
          this.snackBar.open(
            this.mapSaveError(err),
            'Dismiss',
            { duration: 5000 }
          );
        },
      });
  }

  /**
   * Translate a save error into a user-friendly snackbar message keyed
   * on the HTTP status. Falls back to a generic message for unexpected
   * status codes / shapes. Keeping the mapping here — and out of the
   * service — preserves the "single error presentation" invariant from
   * F8 (the snackbar is the only place the user sees the failure).
   *
   * Uses `statusText` rather than `message` so the snackbar reads
   * `"Failed to save file: 500 Server Error"` instead of Angular's
   * verbose default `"Http failure response for /…: 500 Server Error"`.
   */
  private mapSaveError(err: unknown): string {
    const httpErr = err instanceof HttpErrorResponse ? err : null;
    const status = httpErr?.status;
    if (status === 413) return 'File too large';
    if (status === 403) return 'Permission denied';
    if (status === 404) return 'Project or file not found';
    if (status === 0) return 'Network error — check connection';
    const statusText = httpErr?.statusText ?? '';
    return `Failed to save file: ${status ?? 'unknown'}${statusText ? ' ' + statusText : ''}`;
  }

  /**
   * Ctrl/Cmd+S — trigger save. Prevented from the browser default so
   * the page does not try to save as HTML. Active only when the Save
   * button is logically visible (a file is selected).
   *
   * No-op while the workspace overlay is hidden — the host keeps the
   * component mounted across hide/show cycles, so the window-level
   * handler would otherwise fire for keystrokes the user did not
   * intend against this overlay.
   */
  @HostListener('window:keydown', ['$event'])
  onSaveKeydown(event: KeyboardEvent): void {
    if (!this.visible) return;
    if (!(event.ctrlKey || event.metaKey) || event.key !== 's') {
      return;
    }
    // In VS Code mode, let the keydown reach code-server so its own
    // save handler runs. Do NOT call preventDefault() — we want the
    // event to flow through normally.
    if (this.editorMode() !== 'builtin') return;
    event.preventDefault();
    if (this.canSave()) {
      this.saveFile();
    }
  }

  /**
   * Escape — dismiss the VS Code overlay so the user can return to the
   * project list. Active only in VS Code mode; in builtin mode the
   * Escape key is left alone so it does not interfere with editor
   * overlays (find dialog, etc.).
   *
   * No-op while the workspace overlay is hidden — the host keeps the
   * component mounted across hide/show cycles, so the window-level
   * handler would otherwise dismiss an already-hidden overlay (or
   * fire when the user is doing something unrelated in the chat).
   */
  @HostListener('window:keydown.escape')
  onEscapeKey(): void {
    if (!this.visible) return;
    if (this.editorMode() === 'vscode') {
      this.onHide();
    }
  }

  /**
   * Switch the workspace to `projectId`. If a cached snapshot exists for
   * this project, it is restored (signals + tree UI) and only SSE is
   * re-established. Otherwise a fresh `getFileTree` HTTP request is
   * fired and the SSE stream is connected.
   *
   * No-op when `projectId` is empty.
   *
   * `previousProjectId` is the project ID we are switching away from.
   * It is used in the cache-MISS branch to capture the outgoing
   * project's component-owned UI state BEFORE the service's
   * `getFileTree` runs (which will internally save the outgoing
   * project's signals but has no reference to the FileTreeComponent or
   * the component-owned viewMode). ngOnInit passes no previous id, so
   * the very first load never tries to save outgoing state.
   *
   * Both branches handle outgoing UI capture:
   *
   *   - Cache-HIT: the component captures `outgoingUiExtras` and hands
   *     them to `restoreState` (cache hit), which threads them into the
   *     internal saveCurrentState call for the outgoing project.
   *
   *   - Cache-MISS: the component captures `outgoingUiExtras` and calls
   *     `saveCurrentState(previousProjectId, outgoingUiExtras)`
   *     directly. `getFileTree` is then allowed to save the service's
   *     own signals for the outgoing project again; because `??`
   *     fallbacks prefer caller-provided extras over the prior cache,
   *     the just-captured expanded paths / viewMode survive both saves.
   *
   * Bug 1 (file content restoration): `currentFile` is deliberately not
   * cached — it can be arbitrarily large and is cheap to refetch. After
   * `restoreState` reapplies the cached `selectedPath`, we refetch the
   * file content via `getFileContent`. `currentFile` is initially null
   * during restore so the viewer shows a loading state until the
   * response lands. We use `takeUntilDestroyed` so an in-flight fetch
   * is cancelled if the component is destroyed mid-switch.
   *
   * Bug 2 (tree expansion restoration): `FileTreeComponent` owns the
   * expanded-path set, so the service cannot read it. The component
   * captures `getExpandedPaths()` from the outgoing project's tree
   * BEFORE the service saves/clears the outgoing signals, then passes
   * them through as `uiExtras` so the service's internal
   * save-current-state includes them in the outgoing project's
   * snapshot.
   */
  private loadProject(projectId: string, previousProjectId?: string): void {
    if (!projectId) {
      return;
    }

    if (this.workspace.hasCachedState(projectId)) {
      // Capture outgoing-project UI state BEFORE restoreState resets the
      // service signals. The FileTreeComponent still displays the
      // outgoing project's tree, so its expanded paths belong to that
      // project. Passing these into restoreState threads them through
      // to the internal saveCurrentState() call for the outgoing id.
      const outgoingUiExtras = {
        expandedPaths: this.fileTree?.getExpandedPaths() ?? [],
        viewMode: this.viewMode(),
      };

      const restored = this.workspace.restoreState(projectId, outgoingUiExtras);
      if (restored) {
        this.viewMode.set(restored.viewMode);
        if (restored.tree) {
          // If the FileTreeComponent isn't currently mounted (the user is
          // in vscode mode), skip the setTree call; the effect above
          // will re-apply the cached tree + expanded paths the next
          // time they switch back to builtin mode.
          if (this.fileTree) {
            this.fileTree.setTree(restored.tree);
            if (restored.expandedPaths.length > 0) {
              this.fileTree.restoreExpandedPaths(restored.expandedPaths);
            }
          }
        }

        // Bug 1 — refetch the previously-selected file's content. The
        // LRU cache intentionally omits `currentFile` to keep entries
        // small and avoid stale-content risk; we refetch instead. Only
        // one fetch fires here — there is no `selectedPath`-watching
        // effect that would re-trigger.
        //
        // With multi-file tabs, `restoreState` repopulates the service's
        // tab list and active path but intentionally leaves content out of
        // the cache. Add the path if an older snapshot does not include it,
        // activate it, and use `ensureTabContent` as the single hydration
        // entry point. Unlike `openFile(projectId, path)`, this avoids a
        // duplicate request when the tab already exists.
        const restoredActivePath = restored.activeFilePath ?? restored.selectedPath;
        if (restoredActivePath) {
          if (!this.workspace.isTabOpen(restoredActivePath)) {
            this.workspace.openTab(restoredActivePath);
          }
          this.workspace.setActiveFile(restoredActivePath);
          this.workspace.ensureTabContent(projectId, restoredActivePath);
        }

        // SSE needs the new projectId targeted for file-change refresh.
        // Skip when the overlay is hidden — the host keeps us mounted
        // across hide/show, and `ngOnChanges` re-connects on re-show.
        if (this.visible) {
          this.workspace.connectSSE(projectId);
        }
        this.loadValidatedWorkdir(projectId);
        return;
      }
    }

    // Cache-miss branch — capture outgoing UI state BEFORE the service's
    // `getFileTree` runs. `getFileTree` internally calls
    // `saveCurrentState(_currentProjectId)` with no extras when the
    // project id changes, so we must thread the FileTreeComponent's
    // expanded paths and the component-owned viewMode in here
    // ourselves. The save is keyed on `previousProjectId` (the
    // OUTGOING project), never on the incoming project, and is skipped
    // on the initial load when `previousProjectId` is undefined.
    if (previousProjectId) {
      const outgoingUiExtras = {
        expandedPaths: this.fileTree?.getExpandedPaths() ?? [],
        viewMode: this.viewMode(),
      };
      this.workspace.saveCurrentState(previousProjectId, outgoingUiExtras);
    }

    this.workspace
      .getFileTree(projectId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((res) => this.fileTree?.setTree(res.tree));
    // Skip when the overlay is hidden — the host keeps us mounted
    // across hide/show, and `ngOnChanges` re-connects on re-show.
    if (this.visible) {
      this.workspace.connectSSE(projectId);
    }
    this.loadValidatedWorkdir(projectId);
  }
}