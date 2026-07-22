import { Component, ElementRef, OnDestroy, ViewChild, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { EditorView, lineNumbers } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { MergeView } from '@codemirror/merge';
import { oneDark } from '@codemirror/theme-one-dark';
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
export class DiffViewerComponent implements OnDestroy {
  private readonly workspace = inject(WorkspaceService);
  public readonly diff = this.workspace.currentDiff.asReadonly();

  private readonly mergeContainer = signal<ElementRef<HTMLElement> | null>(null);
  private mergeView: MergeView | null = null;

  @ViewChild('mergeContainer')
  private set container(container: ElementRef<HTMLElement> | undefined) {
    this.mergeContainer.set(container ?? null);
  }

  private readonly refreshMergeView = effect(() => {
    const d = this.diff();
    const container = this.mergeContainer();

    this.destroyMergeView();
    if (!d || !d.has_changes || d.error || !container) return;

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
      parent: container.nativeElement,
    });
  });

  ngOnDestroy(): void {
    this.destroyMergeView();
  }

  private destroyMergeView(): void {
    this.mergeView?.destroy();
    this.mergeView = null;
  }
}
