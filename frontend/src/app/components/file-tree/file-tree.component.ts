import { Component, EventEmitter, Input, Output, effect, inject, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTreeFlatDataSource, MatTreeFlattener, MatTreeModule } from '@angular/material/tree';
import { FlatTreeControl } from '@angular/cdk/tree';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { finalize } from 'rxjs';

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
      <mat-tree-node *matTreeNodeDef="let node" matTreeNodePadding
                     [class.file-open]="isFileOpen(node.path)"
                     [class.file-active]="isActiveFile(node.path)">
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
        <span class="dirname" (click)="toggleNode(node)">{{ node.name }}</span>
      </mat-tree-node>
    </mat-tree>
  `,
  styleUrl: './file-tree.component.scss',
})
export class FileTreeComponent {
  @Input() public projectId = '';
  @Output() public readonly fileSelected = new EventEmitter<string>();

  /** Paths of files that have open tabs in the workspace editor. */
  readonly openPaths = input<string[]>([]);

  /** Path of the currently focused/active file in the workspace editor. */
  readonly activePath = input<string | null>(null);

  private readonly workspace = inject(WorkspaceService);

  private readonly transformer = new MatTreeFlattener(
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

  public readonly treeControl = new FlatTreeControl<FlatNode>(
    (node) => node.level,
    (node) => node.expandable
  );

  public readonly dataSource = new MatTreeFlatDataSource(this.treeControl, this.transformer);

  public readonly hasChild = (_: number, node: FlatNode): boolean => node.expandable;

  private readonly _fileChangeEffect = effect(() => {
    const change = this.workspace.fileChanged();
    if (change) {
      this.refreshAffectedNode(change.path);
    }
  });

  setTree(tree: FileTreeNode[]): void {
    this._expandedPaths.clear();
    this._nestedTree = [...tree];
    this.dataSource.data = tree;
  }

  /**
   * Read-only snapshot of currently-expanded directory paths, used by the
   * workspace LRU cache to preserve the tree's expansion state across
   * project switches. The returned array is a fresh copy — mutating it has
   * no effect on the component.
   */
  getExpandedPaths(): string[] {
    return Array.from(this._expandedPaths);
  }

  /**
   * Replace the set of expanded paths and re-apply expansion to the current
   * tree. Safe to call AFTER `setTree()` so the flat data nodes exist when
   * `_restoreExpanded()` walks them. Used by `WorkspaceComponent` when
   * restoring a cached workspace state.
   */
  restoreExpandedPaths(paths: string[]): void {
    this._expandedPaths.clear();
    for (const p of paths) {
      this._expandedPaths.add(p);
    }
    if (this.dataSource.data && this.dataSource.data.length > 0) {
      this._restoreExpanded();
    }
  }

  toggleNode(node: FlatNode): void {
    if (!node.loaded) {
      if (this._inFlightPaths.has(node.path)) return;

      this._inFlightPaths.add(node.path);
      this.workspace.expandDirectory(this.projectId, {
        name: node.name, path: node.path, type: 'directory', size: null, children: null
      }).pipe(
        finalize(() => this._inFlightPaths.delete(node.path))
      ).subscribe({
        next: res => {
          node.loaded = true;
          this.updateNodeChildren(node.path, res.tree);
          if (this.treeControl.isExpanded(node)) {
            this._expandedPaths.add(node.path);
          }
        },
        error: () => {
          this._expandedPaths.delete(node.path);
        },
      });
      return;
    }

    this.treeControl.toggle(node);
    if (this.treeControl.isExpanded(node)) {
      this._expandedPaths.add(node.path);
    } else {
      this._expandedPaths.delete(node.path);
    }
  }

  selectFile(node: FlatNode): void {
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

  /** True if the file at `path` has an open tab in the workspace editor. */
  isFileOpen(path: string): boolean {
    return this.openPaths().includes(path);
  }

  /** True if `path` matches the currently focused/active file. */
  isActiveFile(path: string): boolean {
    return this.activePath() === path;
  }

  refreshAffectedNode(changedPath: string): void {
    if (!this.projectId) return;
    const parentPath = this._parentDir(changedPath);
    if (parentPath === null) {
      this.workspace.getFileTree(this.projectId, '.').subscribe({
        next: (res) => {
          this._nestedTree = [...res.tree];
          this.dataSource.data = this._nestedTree;
          this._restoreExpanded();
        },
        error: () => undefined,
      });
      return;
    }

    this.workspace.getFileTree(this.projectId, parentPath).subscribe({
      next: (res) => this.updateNodeChildren(parentPath, res.tree),
      error: () => undefined,
    });
  }

  private _parentDir(path: string): string | null {
    if (!path || path.indexOf('/') === -1) return null;
    const index = path.lastIndexOf('/');
    return index === 0 ? '' : path.slice(0, index);
  }

  private _restoreExpanded(): void {
    this.treeControl.dataNodes.forEach((currentNode) => {
      if (currentNode.expandable && this._expandedPaths.has(currentNode.path)) {
        this.treeControl.expand(currentNode);
      }
    });
  }

  private updateNodeChildren(path: string, children: FileTreeNode[]): void {
    /** Rebuild MatTreeFlatDataSource with updated children for a directory.

     Walks the flat data, finds the node matching path, and rebuilds the
     tree data so the flattener picks up the new children. Since
     MatTreeFlatDataSource.data expects the nested (non-flat) structure, we
     store the original nested tree and patch it before reassigning.
    */
    if (!this._nestedTree) {
      this._nestedTree = [];
    }

    const patch = (nodes: FileTreeNode[]): FileTreeNode[] =>
      nodes.map(currentNode => {
        if (currentNode.path === path) {
          return { ...currentNode, children };
        }
        if (currentNode.children) {
          return { ...currentNode, children: patch(currentNode.children) };
        }
        return currentNode;
      });
    this._nestedTree = patch(this._nestedTree);
    this.dataSource.data = this._nestedTree;
    this._restoreExpanded();
  }

  private _nestedTree: FileTreeNode[] | null = null;
  private readonly _expandedPaths = new Set<string>();
  private readonly _inFlightPaths = new Set<string>();
}
