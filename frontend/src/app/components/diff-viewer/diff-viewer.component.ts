import {
  Component,
  ElementRef,
  OnDestroy,
  ViewChild,
  effect,
  inject,
  input,
  signal,
  computed as _computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { EditorView, lineNumbers } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { MergeView } from '@codemirror/merge';
import { oneDark } from '@codemirror/theme-one-dark';
import { WorkspaceService } from '../../services/workspace.service';

/**
 * Reusable two-pane diff viewer — `@codemirror/merge` MergeView.
 *
 * Two operating modes:
 *
 * * ``'workspace'`` (default) — consumes ``WorkspaceService.currentDiff``
 *   (the workspace git HEAD-vs-working diff). Unchanged behavior for the
 *   workspace overlay; zero risk for the existing consumer.
 *
 * * ``'persona'`` — uses the ``aContent`` / ``bContent`` inputs to render
 *   an arbitrary content-pair diff. The Persona page passes the row's
 *   proposed content vs the effective baseline (the diff-of-record for
 *   approval review). Both panes are read-only.
 *
 * Generalization is opt-in: omitting all persona inputs keeps the legacy
 * workspace path intact (the WorkspaceService signal drives the view).
 *
 * Renders ``null`` while content is loading — the parent should hold a
 * local "loading" state. The template also surfaces the error / no-change
 * state the workspace signal already carries.
 */
@Component({
  selector: 'app-diff-viewer',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (shouldRender()) {
      <div class="diff-viewer">
        <div class="diff-header">
          @if (mode() === 'persona') {
            <span class="filepath">{{ aLabel() }} → {{ bLabel() }}</span>
            @if (!hasChanges()) {
              <span class="badge clean">No Changes</span>
            } @else {
              <span class="badge modified">Modified</span>
            }
          } @else {
            <span class="filepath">{{ diffPath() }}</span>
            @if (diffError() === 'not_a_git_repo') {
              <span class="badge">Not a Git Repo</span>
            } @else if (!diffHasChanges()) {
              <span class="badge clean">No Changes</span>
            } @else {
              <span class="badge modified">Modified</span>
            }
          }
        </div>

        <!-- Persona mode: standalone content-pair diff. -->
        @if (mode() === 'persona') {
          @if (hasChanges()) {
            <div #mergeContainer class="merge-container"></div>
          } @else {
            <div class="no-changes">Content matches the active baseline — no diff.</div>
          }
        } @else {
          <!-- Legacy workspace mode — conditional render keeps the prior
             test contract (renders nothing when diff is null). -->
          @if (diffHasChanges() && !diffError()) {
            <div #mergeContainer class="merge-container"></div>
          } @else if (!diffError()) {
            <div class="no-changes">File matches HEAD — no uncommitted changes.</div>
          } @else {
            <div class="no-git">This directory is not a git repository.</div>
          }
        }
      </div>
    }
  `,
  styleUrl: './diff-viewer.component.scss',
})
export class DiffViewerComponent implements OnDestroy {
  private readonly workspace = inject(WorkspaceService);
  public readonly diff = this.workspace.currentDiff.asReadonly();

  /** Operating mode — 'workspace' (default) or 'persona'. */
  readonly mode = input<'workspace' | 'persona'>('workspace');

  /** Persona-mode: "A" pane content (typically the active baseline). */
  readonly aContent = input<string>('');

  /** Persona-mode: "B" pane content (typically the proposed row). */
  readonly bContent = input<string>('');

  /** Persona-mode: short label for the A pane (header breadcrumb). */
  readonly aLabel = input<string>('A');

  /** Persona-mode: short label for the B pane (header breadcrumb). */
  readonly bLabel = input<string>('B');

  /** Persona-mode: shows "Modified" badge. Derived from the inputs. */
  readonly hasChanges = input<boolean>(true);

  /** True when the host should render anything. Persona mode is unconditional;
   * workspace mode mirrors the original `compare()`-when-null behavior. */
  readonly shouldRender = _computed(() => this.mode() === 'persona' || this.diff() != null);

  /** Workspace-mode: cached path/has_changes/error so the template doesn't
   * have to read the service signal inside branches. */
  readonly diffPath = _computed(() => this.diff()?.path ?? '');
  readonly diffHasChanges = _computed(() => this.diff()?.has_changes ?? false);
  readonly diffError = _computed(() => this.diff()?.error ?? null);

  private readonly mergeContainer = signal<ElementRef<HTMLElement> | null>(null);
  private mergeView: MergeView | null = null;

  @ViewChild('mergeContainer')
  private set container(container: ElementRef<HTMLElement> | undefined) {
    this.mergeContainer.set(container ?? null);
  }

  /**
   * Effect: tears down + rebuilds MergeView whenever inputs OR the
   * workspace diff change. We use a single effect with eager
   * destructure to keep all reactive reads in one place — Angular
   * signals need their full dependency list visible inside the effect
   * body or the change-detection graph goes stale.
   */
  private readonly refreshMergeView = effect(() => {
    const m = this.mode();
    const container = this.mergeContainer();

    this.destroyMergeView();
    if (!container) return;

    if (m === 'persona') {
      if (!this.hasChanges()) return;
      this.mergeView = new MergeView({
        a: {
          doc: this.aContent(),
          extensions: [
            EditorView.editable.of(false),
            EditorState.readOnly.of(true),
            lineNumbers(),
            oneDark,
          ],
        },
        b: {
          doc: this.bContent(),
          extensions: [
            EditorView.editable.of(false),
            EditorState.readOnly.of(true),
            lineNumbers(),
            oneDark,
          ],
        },
        parent: container.nativeElement,
      });
      return;
    }

    // Legacy 'workspace' path — untouched consumer behavior.
    const d = this.diff();
    if (!d || !d.has_changes || d.error) return;
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