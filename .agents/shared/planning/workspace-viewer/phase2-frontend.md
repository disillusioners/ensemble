# Phase 2: Frontend Viewer — CodeMirror 6, File Tree, Code/Diff Viewers

## Objective

Build the Angular frontend for the workspace viewer: a CodeMirror 6 integration for read-only code rendering, a recursive file tree component with lazy directory expansion, a code viewer with syntax highlighting, and a diff viewer using CodeMirror's merge-view extension.

## Coupling

- **Depends on**: Phase 1 (API contract only — not implementation)
- **Coupling type**: **loose** — Phase 2 depends on the REST response shapes defined in `workspace_schemas.py`, not on Phase 1's actual code. Phase 2 can start immediately using mock data matching the API contract.
- **Shared files with other phases**: None (frontend-only files)
- **Shared APIs/interfaces**: Consumes `GET /api/workspace/{project_id}/tree`, `GET /api/workspace/{project_id}/file`, `GET /api/workspace/{project_id}/diff`
- **Why this coupling**: The frontend and backend are separate codebases. The only coupling is the HTTP contract. Once the schema is agreed (Phase 1 Task 2), Phase 2 can proceed independently.

## Context

### Existing Frontend Patterns to Follow

**Standalone component + lazy route** (`frontend/src/app/app.routes.ts`):
```typescript
{ path: 'jobs', loadComponent: () => import('./pages/jobs/jobs.component').then(m => m.JobsComponent) },
```

**Service pattern** (`frontend/src/app/services/project.service.ts`):
```typescript
@Injectable({ providedIn: 'root' })
export class ProjectService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/projects';

  readonly projects = signal<Project[]>([]);
  readonly loading = signal(false);

  listProjects(): Observable<ProjectListResponse> {
    return this.http.get<ProjectListResponse>(this.API_BASE).pipe(
      tap((response) => this.projects.set(response.projects)),
    );
  }
}
```

**Model pattern** (`frontend/src/app/models/project.model.ts`):
```typescript
export interface Project {
  project_id: string;
  name: string;
  main_directory: string | null;
  // ...
}
```

**Angular config** (`frontend/src/app/app.config.ts`):
```typescript
providers: [
  provideRouter(routes),
  provideHttpClient(),
  provideAnimations(),
  provideMarkdown({ /* mermaid config */ }),
]
```

**Component structure**: Pages in `pages/`, reusable components in `components/`, services in `services/`, models in `models/`.

**Existing CSS theme**: Dark theme via ng-zorro-antd global CSS. Component styles use `:host` scoping. Material components used for UI elements.

**No CodeMirror dependency yet**: `frontend/package.json` has no code editor package. We need to add `@codemirror/state`, `@codemirror/view`, `@codemirror/lang-*`, and `@codemirror/merge`.

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add CodeMirror 6 dependencies | Install `@codemirror/state`, `@codemirror/view`, `@codemirror/commands`, `@codemirror/language`, `@codemirror/merge`, and language packages. | `frontend/package.json` (modify) |
| 2 | Create workspace models | TypeScript interfaces matching backend schemas | `frontend/src/app/models/workspace.model.ts` (new) |
| 3 | Create WorkspaceService | HTTP client for tree/file/diff endpoints. Signal-based state. | `frontend/src/app/services/workspace.service.ts` (new) |
| 4 | Create CodeMirror directive | Angular directive wrapping `EditorView` in read-only mode. Language auto-detection. | `frontend/src/app/components/code-viewer/codemirror.directive.ts` (new) |
| 5 | Create CodeViewerComponent | Displays file content using the CodeMirror directive. Shows file metadata (size, lines, language). | `frontend/src/app/components/code-viewer/code-viewer.component.ts` (new) |
| 6 | Create DiffViewerComponent | Displays inline git diff using `@codemirror/merge` `MergeView`. Shows added/removed/unchanged lines. | `frontend/src/app/components/diff-viewer/diff-viewer.component.ts` (new) |
| 7 | Create FileTreeComponent | Recursive Material tree with lazy directory expansion. Click file → emit selected path. | `frontend/src/app/components/file-tree/file-tree.component.ts` (new) |
| 8 | Create WorkspacePageComponent | Top-level page: split layout (tree sidebar + content area). Tab switcher between "Code" and "Diff" views. | `frontend/src/app/pages/workspace/workspace.component.ts` (new) |
| 9 | Add route | Register `/projects/:projectId/workspace` route | `frontend/src/app/app.routes.ts` (modify) |
| 10 | Write frontend tests | Jest unit tests for service, component specs | `frontend/src/app/services/workspace.service.spec.ts`, component specs (new) |

---

## Task Details

### Task 1: CodeMirror 6 Dependencies

```bash
cd frontend
npm install @codemirror/state @codemirror/view @codemirror/commands \
  @codemirror/language @codemirror/merge @codemirror/theme-one-dark \
  @codemirror/legacy-modes
# Language packages:
npm install @codemirror/lang-python @codemirror/lang-javascript \
  @codemirror/lang-css @codemirror/lang-html @codemirror/lang-json \
  @codemirror/lang-markdown @codemirror/lang-sql @codemirror/lang-yaml
```

**Key Decision**: Use raw `@codemirror/*` packages, NOT `ngx-codemirror`. CodeMirror 6 is framework-agnostic and works cleanly via a thin Angular directive. `ngx-codemirror` adds an unnecessary abstraction layer and is less actively maintained for CM6.

### Task 2: Workspace Models

**New file**: `frontend/src/app/models/workspace.model.ts`

```typescript
export interface FileTreeNode {
  name: string;
  path: string;        // relative to workdir
  type: 'file' | 'directory' | 'symlink';
  size: number | null;  // bytes, files only
  children: FileTreeNode[] | null;  // null = not expanded
}

export interface FileTreeResponse {
  project_id: string;
  path: string;
  tree: FileTreeNode[];
  truncated: boolean;
}

export interface FileContentResponse {
  project_id: string;
  path: string;
  content: string;
  language: string | null;
  total_lines: number;
  offset: number;
  limit: number;
  truncated: boolean;
  binary: boolean;
  size_bytes: number;
}

export interface GitDiffResponse {
  project_id: string;
  path: string;
  has_changes: boolean;
  diff: string | null;
  head_content: string | null;
  working_content: string | null;
  error: string | null;
}
```

### Task 3: WorkspaceService

**New file**: `frontend/src/app/services/workspace.service.ts`

```typescript
import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap, catchError, of } from 'rxjs';
import { FileTreeResponse, FileContentResponse, GitDiffResponse } from '../models/workspace.model';

@Injectable({ providedIn: 'root' })
export class WorkspaceService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/workspace';

  // W14: Track current project ID for SSE auto-refresh callbacks
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
    this._currentProjectId = projectId;  // W14: track for SSE refresh
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
```

### Task 4: CodeMirror Directive

**New file**: `frontend/src/app/components/code-viewer/codemirror.directive.ts`

```typescript
import { Directive, ElementRef, Input, OnChanges, OnDestroy, SimpleChanges } from '@angular/core';
import { EditorState, EditorView, Compartment, Extension } from '@codemirror/state';
import { lineNumbers, highlightActiveLine, highlightActiveLineGutter } from '@codemirror/view';
import { oneDark } from '@codemirror/theme-one-dark';

// Synchronous imports — bundled at build time (v1 approach).
// Total size of all lang-* packages ≈ 800KB minified, well within budget.
import { python } from '@codemirror/lang-python';
import { javascript } from '@codemirror/lang-javascript';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { sql } from '@codemirror/lang-sql';
import { yaml } from '@codemirror/lang-yaml';

/**
 * Thin Angular directive wrapping CodeMirror 6 EditorView in read-only mode.
 *
 * Why a directive, not ngx-codemirror:
 * - CM6 is framework-agnostic by design
 * - Direct EditorView access gives full control over extensions
 * - No third-party wrapper dependency to maintain
 */
@Directive({
  selector: '[appCodemirror]',
  standalone: true,
})
export class CodemirrorDirective implements OnChanges, OnDestroy {
  @Input() content = '';
  @Input() language: string | null = null;

  private view: EditorView | null = null;
  private langCompartment = new Compartment();

  constructor(private el: ElementRef<HTMLElement>) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.view) {
      this.initView();
    }
    if (changes['content'] && this.view) {
      this.view.dispatch({
        changes: { from: 0, to: this.view.state.doc.length, insert: this.content }
      });
    }
    if (changes['language'] && this.view) {
      this.view.dispatch({
        effects: this.langCompartment.reconfigure(this.getLangExtension())
      });
    }
  }

  private initView(): void {
    this.view = new EditorView({
      state: EditorState.create({
        doc: this.content,
        extensions: [
          EditorView.editable.of(false),  // READ-ONLY
          EditorState.readOnly.of(true),
          lineNumbers(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          oneDark,
          this.langCompartment.of(this.getLangExtension()),
          EditorView.lineWrapping,
        ],
      }),
      parent: this.el.nativeElement,
    });
  }

  private getLangExtension(): Extension {
    // Synchronous language lookup — all packages imported at top of file.
    const langMap: Record<string, Extension> = {
      python: python(),
      typescript: javascript({ typescript: true }),
      javascript: javascript(),
      html: html(),
      css: css(),
      json: json(),
      markdown: markdown(),
      sql: sql(),
      yaml: yaml(),
    };
    if (this.language && langMap[this.language]) {
      return langMap[this.language];
    }
    return [];  // plain text — still gets line numbers + dark theme
  }

  ngOnDestroy(): void {
    this.view?.destroy();
  }
}
```

**Key Decision**: For v1, bundle language packages directly. The total size of `@codemirror/lang-*` packages is under 1MB — well within the 10MB client budget. Lazy-loading language packages can be an optimization in a later phase.

### Task 5: CodeViewerComponent

**New file**: `frontend/src/app/components/code-viewer/code-viewer.component.ts`

```typescript
import { Component, Input, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceService } from '../../services/workspace.service';
import { CodemirrorDirective } from './codemirror.directive';

@Component({
  selector: 'app-code-viewer',
  standalone: true,
  imports: [CommonModule, CodemirrorDirective],
  template: `
    @if (file(); as f) {
      <div class="code-viewer">
        <div class="code-header">
          <span class="filepath">{{ f.path }}</span>
          <span class="meta">{{ f.total_lines }} lines · {{ formatSize(f.size_bytes) }}</span>
          @if (f.binary) {
            <span class="badge binary">Binary</span>
          }
          @if (f.truncated) {
            <span class="badge truncated">Truncated</span>
          }
        </div>
        @if (!f.binary) {
          <div class="code-content" [appCodemirror]=""
               [content]="f.content"
               [language]="f.language"></div>
        } @else {
          <div class="binary-placeholder">
            Binary file — preview not available ({{ formatSize(f.size_bytes) }})
          </div>
        }
      </div>
    }
  `,
  styleUrl: './code-viewer.component.scss',
})
export class CodeViewerComponent {
  private workspace = inject(WorkspaceService);
  readonly file = this.workspace.currentFile.asReadonly();

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }
}
```

### Task 6: DiffViewerComponent

**New file**: `frontend/src/app/components/diff-viewer/diff-viewer.component.ts`

```typescript
import { Component, Input, ElementRef, ViewChild, AfterViewInit, OnChanges, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { EditorView } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { MergeView } from '@codemirror/merge';
import { oneDark } from '@codemirror/theme-one-dark';
import { lineNumbers } from '@codemirror/view';
import { WorkspaceService } from '../../services/workspace.service';

@Component({
  selector: 'app-diff-viewer',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (diff(); as d) {
      <div class="diff-viewer">
        <div class="diff-header">
          <span class="filepath">{{ d.path }}</span>
          @if (d.error === 'not_a_git_repo') {
            <span class="badge">Not a Git Repo</span>
          } @else if (!d.has_changes) {
            <span class="badge clean">No Changes</span>
          } @else {
            <span class="badge modified">Modified</span>
          }
        </div>
        @if (d.has_changes && !d.error) {
          <div #mergeContainer class="merge-container"></div>
        } @else if (!d.error) {
          <div class="no-changes">File matches HEAD — no uncommitted changes.</div>
        } @else {
          <div class="no-git">This directory is not a git repository.</div>
        }
      </div>
    }
  `,
  styleUrl: './diff-viewer.component.scss',
})
export class DiffViewerComponent implements AfterViewInit, OnChanges {
  private workspace = inject(WorkspaceService);
  readonly diff = this.workspace.currentDiff.asReadonly();

  @ViewChild('mergeContainer', { static: false }) container!: ElementRef;

  private mergeView: MergeView | null = null;

  ngAfterViewInit(): void {
    this.renderDiff();
  }

  ngOnChanges(): void {
    if (this.container) {
      this.renderDiff();
    }
  }

  private renderDiff(): void {
    const d = this.diff();
    if (!d || !d.has_changes || d.error || !this.container) return;

    // Destroy previous view
    this.mergeView?.destroy();

    this.mergeView = new MergeView({
      a: {
        doc: d.head_content || '',
        extensions: [
          EditorView.editable.of(false),
          EditorState.readOnly.of(true),
          lineNumbers(),
          oneDark,
        ],
      },
      b: {
        doc: d.working_content || '',
        extensions: [
          EditorView.editable.of(false),
          EditorState.readOnly.of(true),
          lineNumbers(),
          oneDark,
        ],
      },
      parent: this.container.nativeElement,
    });
  }
}
```

**Key Decision**: Use `@codemirror/merge` `MergeView` for side-by-side diff. This is the official CM6 merge extension, purpose-built for diffs. The left pane shows HEAD, the right pane shows the working tree. Both are read-only.

### Task 7: FileTreeComponent

**New file**: `frontend/src/app/components/file-tree/file-tree.component.ts`

```typescript
import { Component, Input, Output, EventEmitter, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTreeModule, MatTreeFlatDataSource, MatTreeFlattener } from '@angular/material/tree';
import { FlatTreeControl } from '@angular/cdk/tree';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeNode } from '../../models/workspace.model';

interface FlatNode {
  expandable: boolean;
  name: string;
  path: string;
  type: string;
  level: number;
  loaded: boolean;
}

@Component({
  selector: 'app-file-tree',
  standalone: true,
  imports: [CommonModule, MatTreeModule, MatIconModule, MatButtonModule, MatProgressSpinnerModule],
  template: `
    <mat-tree [dataSource]="dataSource" [treeControl]="treeControl">
      <mat-tree-node *matTreeNodeDef="let node" matTreeNodePadding>
        <button mat-icon-button disabled></button>
        <mat-icon class="file-icon">{{ getFileIcon(node.type, node.name) }}</mat-icon>
        <span class="filename" (click)="selectFile(node)">{{ node.name }}</span>
      </mat-tree-node>

      <mat-tree-node *matTreeNodeDef="let node; when: hasChild" matTreeNodePadding>
        <button mat-icon-button [attr.aria-label]="'Toggle ' + node.name"
                (click)="toggleNode(node)">
          <mat-icon>{{ treeControl.isExpanded(node) ? 'expand_more' : 'chevron_right' }}</mat-icon>
        </button>
        <mat-icon class="dir-icon">folder</mat-icon>
        <span class="dirname">{{ node.name }}</span>
      </mat-tree-node>
    </mat-tree>
  `,
  styleUrl: './file-tree.component.scss',
})
export class FileTreeComponent {
  @Input() projectId: string = '';
  @Output() fileSelected = new EventEmitter<string>();

  private workspace = inject(WorkspaceService);

  private transformer = new MatTreeFlattener(
    (node: FileTreeNode, level: number): FlatNode => ({
      expandable: node.type === 'directory',
      name: node.name,
      path: node.path,
      type: node.type,
      level,
      loaded: node.children !== null,
    }),
    (node: FlatNode) => node.level,
    (node: FlatNode) => node.expandable,
    (node: FileTreeNode) => node.children || []
  );

  treeControl = new FlatTreeControl<FlatNode>(
    (node) => node.level,
    (node) => node.expandable
  );

  dataSource = new MatTreeFlatDataSource(this.treeControl, this.transformer);

  hasChild = (_: number, node: FlatNode) => node.expandable;

  setTree(tree: FileTreeNode[]) {
    this._nestedTree = tree;
    this.dataSource.data = tree;
  }

  async toggleNode(node: FlatNode) {
    if (!node.loaded) {
      // Lazy-load children
      this.workspace.expandDirectory(this.projectId, {
        name: node.name, path: node.path, type: 'directory', size: null, children: null
      }).subscribe(res => {
        node.loaded = true;
        // Update tree data with loaded children
        this.updateNodeChildren(node.path, res.tree);
        this.treeControl.toggle(node);
      });
    } else {
      this.treeControl.toggle(node);
    }
    // Track expanded state for rebuild preservation
    if (this.treeControl.isExpanded(node)) {
      this._expandedPaths.add(node.path);
    } else {
      this._expandedPaths.delete(node.path);
    }
  }

  selectFile(node: FlatNode) {
    this.fileSelected.emit(node.path);
  }

  getFileIcon(type: string, name: string): string {
    if (type !== 'file') return 'folder';
    const ext = name.split('.').pop()?.toLowerCase();
    const iconMap: Record<string, string> = {
      py: 'description', ts: 'code', js: 'code', html: 'html',
      css: 'style', json: 'data_object', md: 'article',
      sql: 'storage', sh: 'terminal', yaml: 'settings',
    };
    return iconMap[ext || ''] || 'insert_drive_file';
  }

  private updateNodeChildren(path: string, children: FileTreeNode[]) {
    /** Rebuild MatTreeFlatDataSource with updated children for a directory.

     Walks the flat data, finds the node matching *path*, and rebuilds the
     tree data so the flattener picks up the new children. Since
     MatTreeFlatDataSource.data expects the nested (non-flat) structure, we
     store the original nested tree and patch it before reassigning.
    */
    // Store nested tree if not yet stored
    if (!this._nestedTree) {
      this._nestedTree = [...this.dataSource.data as unknown as FileTreeNode[]];
    }
    // Recursively find and patch the node
    const patch = (nodes: FileTreeNode[]): FileTreeNode[] =>
      nodes.map(node => {
        if (node.path === path) {
          return { ...node, children };
        }
        if (node.children) {
          return { ...node, children: patch(node.children) };
        }
        return node;
      });
    this._nestedTree = patch(this._nestedTree);
    this.dataSource.data = this._nestedTree;
    // Re-expand any previously expanded nodes that got rebuilt
    this.treeControl.dataNodes.forEach(n => {
      if (n.expandable && this._expandedPaths.has(n.path)) {
        this.treeControl.expand(n);
      }
    });
  }

  private _nestedTree: FileTreeNode[] | null = null;
  private _expandedPaths = new Set<string>();
}
```

### Task 8: WorkspacePageComponent

**New file**: `frontend/src/app/pages/workspace/workspace.component.ts`

```typescript
import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatTabsModule } from '@angular/material/tabs';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';

import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeComponent } from '../../components/file-tree/file-tree.component';
import { CodeViewerComponent } from '../../components/code-viewer/code-viewer.component';
import { DiffViewerComponent } from '../../components/diff-viewer/diff-viewer.component';

@Component({
  selector: 'app-workspace',
  standalone: true,
  imports: [
    CommonModule, MatSidenavModule, MatToolbarModule, MatTabsModule,
    MatIconModule, MatButtonModule,
    FileTreeComponent, CodeViewerComponent, DiffViewerComponent,
  ],
  template: `
    <div class="workspace-container">
      <mat-sidenav-container class="sidenav-container">
        <mat-sidenav mode="side" opened class="file-tree-sidenav">
          <div class="tree-header">
            <mat-icon>folder_open</mat-icon>
            <span>Files</span>
          </div>
          <app-file-tree [projectId]="projectId"
                         (fileSelected)="onFileSelected($event)">
          </app-file-tree>
        </mat-sidenav>

        <mat-sidenav-content class="content-area">
          <mat-toolbar class="content-toolbar">
            <span class="toolbar-title">{{ selectedPath() || 'Select a file' }}</span>
            <span class="spacer"></span>
            @if (selectedPath()) {
              <mat-button-toggle-group>
                <mat-button-toggle value="code" [checked]="viewMode() === 'code'"
                                   (change)="onSelectCode()">
                  <mat-icon>code</mat-icon> Code
                </mat-button-toggle>
                <mat-button-toggle value="diff" [checked]="viewMode() === 'diff'"
                                   (change)="onSelectDiff()">
                  <mat-icon>compare_arrows</mat-icon> Diff
                </mat-button-toggle>
              </mat-button-toggle-group>
            }
          </mat-toolbar>

          <div class="viewer-content">
            @if (viewMode() === 'code') {
              <app-code-viewer></app-code-viewer>
            } @else {
              <app-diff-viewer></app-diff-viewer>
            }
          </div>
        </mat-sidenav-content>
      </mat-sidenav-container>
    </div>
  `,
  styleUrl: './workspace.component.scss',
})
export class WorkspaceComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private workspace = inject(WorkspaceService);

  projectId: string = '';
  readonly selectedPath = this.workspace.selectedPath.asReadonly();
  viewMode = signal<'code' | 'diff'>('code');

  ngOnInit(): void {
    this.projectId = this.route.snapshot.paramMap.get('projectId') || '';
    if (this.projectId) {
      this.workspace.getFileTree(this.projectId).subscribe();
    }
  }

  onFileSelected(path: string): void {
    this.viewMode.set('code');
    this.workspace.getFileContent(this.projectId, path).subscribe();
  }

  /** Blocking Fix 2: Wire Code button to set view mode and ensure file is loaded */
  onSelectCode(): void {
    this.viewMode.set('code');
    // File content is already loaded by onFileSelected — no re-fetch needed
  }

  /** Blocking Fix 2: Wire Diff button to fetch diff data BEFORE switching view.
   * Without this, the DiffViewerComponent renders empty because currentDiff
   * signal was never populated. */
  onSelectDiff(): void {
    const path = this.selectedPath();
    if (path) {
      this.workspace.getFileDiff(this.projectId, path).subscribe({
        next: () => this.viewMode.set('diff'),
        error: () => this.viewMode.set('diff'),  // still switch — shows error state
      });
    } else {
      this.viewMode.set('diff');
    }
  }
}
```

### Task 9: Add Route

**Modify**: `frontend/src/app/app.routes.ts`

```typescript
// Add after the projects route:
{
  path: 'projects/:projectId/workspace',
  loadComponent: () => import('./pages/workspace/workspace.component').then(m => m.WorkspaceComponent),
  title: 'Workspace Viewer'
},
```

### Task 10: Frontend Tests

**New files**:
- `frontend/src/app/services/workspace.service.spec.ts` — Test HTTP calls, signal state updates, error handling
- `frontend/src/app/components/file-tree/file-tree.component.spec.ts` — Test tree rendering, lazy expansion, file selection
- `frontend/src/app/components/code-viewer/code-viewer.component.spec.ts` — Test content rendering, binary file handling
- `frontend/src/app/components/code-viewer/codemirror.directive.spec.ts` — Test directive lifecycle, content updates, language switching
- `frontend/src/app/components/diff-viewer/diff-viewer.component.spec.ts` — Test diff rendering, no-git-repo state
- `frontend/src/app/pages/workspace/workspace.component.spec.ts` — Test page integration, route param extraction

Test pattern follows existing specs (Jest + jest-preset-angular):
```typescript
describe('WorkspaceService', () => {
  let service: WorkspaceService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()]
    });
    service = TestBed.inject(WorkspaceService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('should fetch file tree', () => {
    service.getFileTree('proj-1').subscribe();
    const req = httpMock.expectOne('/api/workspace/proj-1/tree?path=.');
    expect(req.request.method).toBe('GET');
    req.flush({ project_id: 'proj-1', path: '.', tree: [], truncated: false });
    expect(service.currentTree()).toEqual([]);
  });
});
```

**CodeMirror Directive Test** (`frontend/src/app/components/code-viewer/codemirror.directive.spec.ts`):

```typescript
import { CodemirrorDirective } from './codemirror.directive';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component, ViewChild } from '@angular/core';

@Component({
  template: `<div #host [appCodemirror]="" [content]="content" [language]="language"></div>`,
  imports: [CodemirrorDirective],
})
class HostComponent {
  @ViewChild('host') host!: { nativeElement: HTMLElement };
  content = '';
  language: string | null = null;
}

describe('CodemirrorDirective', () => {
  let fixture: ComponentFixture<HostComponent>;
  let host: HostComponent;

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [HostComponent] });
    fixture = TestBed.createComponent(HostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();  // triggers ngOnChanges → initView
  });

  it('should create a CodeMirror EditorView', () => {
    const cmEl = fixture.nativeElement.querySelector('.cm-editor');
    expect(cmEl).toBeTruthy();
  });

  it('should render content text', () => {
    host.content = 'print("hello")';
    fixture.detectChanges();
    const cmContent = fixture.nativeElement.querySelector('.cm-content');
    expect(cmContent.textContent).toContain('print("hello")');
  });

  it('should be read-only (no contentEditable)', () => {
    const cmContent = fixture.nativeElement.querySelector('.cm-content');
    expect(cmContent.getAttribute('contenteditable')).toBe('false');
  });

  it('should update content on input change', () => {
    host.content = 'line 1';
    fixture.detectChanges();
    host.content = 'line 1\nline 2';
    fixture.detectChanges();
    const cmContent = fixture.nativeElement.querySelector('.cm-content');
    expect(cmContent.textContent).toContain('line 2');
  });

  it('should apply language extension when language is set', () => {
    host.content = 'x = 1';
    host.language = 'python';
    fixture.detectChanges();
    // CM applies language-specific highlighting classes
    const cmEl = fixture.nativeElement.querySelector('.cm-editor');
    expect(cmEl).toBeTruthy();
  });

  it('should clean up EditorView on destroy', () => {
    const cmEl = fixture.nativeElement.querySelector('.cm-editor');
    expect(cmEl).toBeTruthy();
    fixture.destroy();
    // After destroy, no CM element should remain
    const cmAfterDestroy = fixture.nativeElement.querySelector('.cm-editor');
    expect(cmAfterDestroy).toBeFalsy();
  });
});
```

---

## Key Files

| File | Action | Purpose |
|------|--------|---------|
| `frontend/package.json` | **MODIFY** | Add @codemirror/* dependencies |
| `frontend/src/app/models/workspace.model.ts` | **CREATE** | TypeScript interfaces for API contract |
| `frontend/src/app/services/workspace.service.ts` | **CREATE** | HTTP + signal-based state |
| `frontend/src/app/components/code-viewer/codemirror.directive.ts` | **CREATE** | CodeMirror 6 directive (read-only) |
| `frontend/src/app/components/code-viewer/code-viewer.component.ts` | **CREATE** | File content viewer |
| `frontend/src/app/components/diff-viewer/diff-viewer.component.ts` | **CREATE** | Git diff viewer (MergeView) |
| `frontend/src/app/components/file-tree/file-tree.component.ts` | **CREATE** | Recursive file tree |
| `frontend/src/app/pages/workspace/workspace.component.ts` | **CREATE** | Page layout + tab switching |
| `frontend/src/app/app.routes.ts` | **MODIFY** | Add workspace route |
| `frontend/src/app/services/workspace.service.spec.ts` | **CREATE** | Service tests |
| Component spec files | **CREATE** | Component tests |

## Constraints

- **Read-only**: CodeMirror views MUST be `editable.of(false)` + `readOnly.of(true)` — no editing in Path B
- **No nz-* components**: Use @angular/material components only. ng-zorro is CSS theme only.
- **Standalone components**: All new components MUST be standalone (matching existing pattern)
- **Lazy route**: The workspace page MUST be lazy-loaded via `loadComponent`
- **Signal-based state**: Use Angular signals for reactive state (matching `project.service.ts` pattern)
- **Dark theme**: CodeMirror MUST use `oneDark` theme to match the existing dark UI
- **Material tree**: Use `MatTreeModule` with `MatTreeFlatDataSource` for the file tree (matches Angular Material patterns)
- **Client size budget**: Total added JS < 2MB (CodeMirror core + languages + merge ≈ 800KB minified)

## Deliverables

- [ ] `frontend/package.json` has @codemirror/* packages installed
- [ ] `workspace.model.ts` matches backend API contract
- [ ] `WorkspaceService` makes correct HTTP calls with proper error handling
- [ ] `CodemirrorDirective` renders read-only code with syntax highlighting
- [ ] `CodeViewerComponent` shows file content with metadata
- [ ] `DiffViewerComponent` shows side-by-side git diff
- [ ] `FileTreeComponent` renders tree with lazy directory expansion
- [ ] `WorkspaceComponent` has split layout with code/diff tabs
- [ ] Route `/projects/:projectId/workspace` works
- [ ] All Jest tests pass
- [ ] No nz-* components used
