import {
  Component,
  DestroyRef,
  EventEmitter,
  HostListener,
  Input,
  OnDestroy,
  OnInit,
  Output,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize } from 'rxjs';
import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeComponent } from '../../components/file-tree/file-tree.component';
import { CodeViewerComponent } from '../../components/code-viewer/code-viewer.component';
import { DiffViewerComponent } from '../../components/diff-viewer/diff-viewer.component';

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
    MatProgressSpinnerModule,
    FileTreeComponent,
    CodeViewerComponent,
    DiffViewerComponent,
  ],
  template: `
    <div class="workspace-container">
      <mat-sidenav-container class="sidenav-container">
        <mat-sidenav mode="side" opened class="file-tree-sidenav">
          <div class="tree-header">
            <mat-icon>folder_open</mat-icon>
            <span>Files</span>
          </div>
          <app-file-tree
            [projectId]="projectId"
            (fileSelected)="onFileSelected($event)"
          ></app-file-tree>
        </mat-sidenav>

        <mat-sidenav-content class="content-area">
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
            @if (viewMode() === 'code') {
              <app-code-viewer></app-code-viewer>
            } @else {
              <app-diff-viewer></app-diff-viewer>
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
export class WorkspaceComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  readonly workspace = inject(WorkspaceService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly snackBar = inject(MatSnackBar);

  @ViewChild(FileTreeComponent, { static: true }) private fileTree!: FileTreeComponent;
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
      this.loadProject(next);
    }
  }

  /**
   * Emitted when the user clicks the Hide button. Hosts using the
   * component as an overlay should listen for this and dismiss the
   * workspace view.
   */
  @Output() public readonly hide = new EventEmitter<void>();

  public readonly selectedPath = this.workspace.selectedPath.asReadonly();
  public readonly currentFile = this.workspace.currentFile.asReadonly();
  public readonly viewMode = signal<'code' | 'diff'>('code');

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

  ngOnInit(): void {
    // Fallback: if no input was set, read from the route. The input
    // always wins — useful for overlay hosts that already know the id.
    if (!this._projectId) {
      this._projectId = this.route.snapshot.paramMap.get('projectId') || '';
    }
    this._initialised = true;
    this.loadProject(this._projectId);
  }

  ngOnDestroy(): void {
    this.workspace.disconnectSSE();
  }

  onFileSelected(path: string): void {
    this.viewMode.set('code');
    this.workspace
      .getFileContent(this.projectId, path)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        error: () => undefined,
      });
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
    if (!this.selectedPath()) {
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
    const path = this.selectedPath();
    if (!path || !this.codeViewer) {
      return;
    }
    this.saving.set(true);
    this.workspace
      .saveFile(this.projectId, path, this.codeViewer.currentContent())
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.saving.set(false))
      )
      .subscribe({
        next: () => {
          // Align the saved-state baseline so `isDirty()` becomes false
          // and a follow-up SSE push of the same file can refresh the
          // editor (this is the round-trip of our own save).
          this.codeViewer!.markSaved();
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
   */
  @HostListener('window:keydown', ['$event'])
  onSaveKeydown(event: KeyboardEvent): void {
    if (!(event.ctrlKey || event.metaKey) || event.key !== 's') {
      return;
    }
    event.preventDefault();
    if (this.canSave()) {
      this.saveFile();
    }
  }

  /**
   * Switch the workspace to `projectId`. If a cached snapshot exists for
   * this project, it is restored (signals + tree UI) and only SSE is
   * re-established. Otherwise a fresh `getFileTree` HTTP request is
   * fired and the SSE stream is connected.
   *
   * No-op when `projectId` is empty.
   */
  private loadProject(projectId: string): void {
    if (!projectId) {
      return;
    }

    if (this.workspace.hasCachedState(projectId)) {
      const restored = this.workspace.restoreState(projectId);
      if (restored) {
        this.viewMode.set(restored.viewMode);
        if (restored.tree) {
          this.fileTree.setTree(restored.tree);
          if (restored.expandedPaths.length > 0) {
            this.fileTree.restoreExpandedPaths(restored.expandedPaths);
          }
        }
        // SSE needs the new projectId targeted for file-change refresh.
        this.workspace.connectSSE(projectId);
        return;
      }
    }

    this.workspace
      .getFileTree(projectId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((res) => this.fileTree.setTree(res.tree));
    this.workspace.connectSSE(projectId);
  }
}