import {
  Component,
  DestroyRef,
  EventEmitter,
  Input,
  OnDestroy,
  OnInit,
  Output,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
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
            <span class="toolbar-title">{{ selectedPath() || 'Select a file' }}</span>
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

  @ViewChild(FileTreeComponent, { static: true }) private fileTree!: FileTreeComponent;

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
  public readonly viewMode = signal<'code' | 'diff'>('code');

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