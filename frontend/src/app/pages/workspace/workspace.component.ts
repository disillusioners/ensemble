import { Component, OnInit, ViewChild, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
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
    CommonModule, MatSidenavModule, MatToolbarModule, MatButtonToggleModule,
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
              <mat-button-toggle-group [value]="viewMode()">
                <mat-button-toggle value="code" (change)="onSelectCode()">
                  <mat-icon>code</mat-icon> Code
                </mat-button-toggle>
                <mat-button-toggle value="diff" (change)="onSelectDiff()">
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
  private readonly route = inject(ActivatedRoute);
  private readonly workspace = inject(WorkspaceService);

  @ViewChild(FileTreeComponent, { static: true }) private fileTree!: FileTreeComponent;

  public projectId = '';
  public readonly selectedPath = this.workspace.selectedPath.asReadonly();
  public readonly viewMode = signal<'code' | 'diff'>('code');

  ngOnInit(): void {
    this.projectId = this.route.snapshot.paramMap.get('projectId') || '';
    if (this.projectId) {
      this.workspace.getFileTree(this.projectId).subscribe(res => this.fileTree.setTree(res.tree));
    }
  }

  onFileSelected(path: string): void {
    this.viewMode.set('code');
    this.workspace.getFileContent(this.projectId, path).subscribe();
  }

  onSelectCode(): void {
    this.viewMode.set('code');
  }

  onSelectDiff(): void {
    const path = this.selectedPath();
    if (path) {
      this.workspace.getFileDiff(this.projectId, path).subscribe({
        next: () => this.viewMode.set('diff'),
        error: () => this.viewMode.set('diff'),
      });
    } else {
      this.viewMode.set('diff');
    }
  }
}
