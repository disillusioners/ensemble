import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
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
        @if (f.binary) {
          <div class="binary-placeholder">
            This file is binary and cannot be edited ({{ formatSize(f.size_bytes) }})
          </div>
        } @else if (f.truncated) {
          <div class="truncated-placeholder">
            This file is truncated and is read-only — showing a preview only
            ({{ formatSize(f.size_bytes) }})
          </div>
        } @else {
          <div class="code-content" [appCodemirror]=""
               [content]="f.content"
               [language]="f.language"
               [editable]="!f.binary && !f.truncated"
               (contentChange)="onContentChange($event)"></div>
        }
      </div>
    }
  `,
  styleUrl: './code-viewer.component.scss',
})
export class CodeViewerComponent {
  private readonly workspace = inject(WorkspaceService);
  private readonly originalContent = signal<string>('');
  public readonly file = this.workspace.currentFile.asReadonly();
  public readonly editedContent = signal<string>('');
  public readonly isDirty = computed(() => this.editedContent() !== this.originalContent());
  public readonly currentContent = this.editedContent.asReadonly();

  /**
   * The path of the file currently bound to `editedContent`. Used by the
   * effect to distinguish a SAME-FILE reload (SSE push or save response)
   * from a DIFFERENT-FILE load. Without this, switching to another file
   * would clobber unsaved edits because the effect cannot tell the two
   * cases apart from `currentFile` alone.
   */
  private readonly currentFilePath = signal<string | null>(null);

  /**
   * The content the editor last agreed was "on disk" — either the freshly
   * loaded file content or the content of the last successful save. The
   * effect compares `editedContent` against this baseline before deciding
   * whether to apply an incoming reload; if the user has unsaved edits
   * (`editedContent !== savedBaseline`), the incoming reload is ignored
   * to preserve keystrokes that arrived after Save but before the SSE
   * push or save response.
   */
  private readonly savedBaseline = signal<string>('');

  constructor() {
    effect(() => {
      const file = this.file();

      // ── Guard 1: no file selected ────────────────────────────────
      // The viewer renders nothing when `file()` is null (template @if
      // skips the block), but we still want every signal to be in a
      // well-defined empty state so stale content never bleeds across
      // selections.
      if (!file) {
        this.originalContent.set('');
        this.editedContent.set('');
        this.savedBaseline.set('');
        this.currentFilePath.set(null);
        return;
      }

      // ── Guard 2: binary or truncated files are read-only ────────
      // F3 — never seed the editor with the previous text file's
      // buffer when the new file is binary; otherwise Save would
      // silently overwrite the binary file with stale text. F4 —
      // never seed the editor with a truncated preview; otherwise
      // Save would destroy the file by writing only the preview
      // slice. Both branches leave the editor buffer empty and
      // `isDirty` false so `canSave()` cannot accidentally fire.
      if (file.binary || file.truncated) {
        this.originalContent.set('');
        this.editedContent.set('');
        this.savedBaseline.set('');
        this.currentFilePath.set(file.path);
        return;
      }

      // ── Different file: full reset ───────────────────────────────
      const incomingPath = file.path;
      const currentPath = this.currentFilePath();

      if (incomingPath !== currentPath) {
        this.currentFilePath.set(incomingPath);
        this.originalContent.set(file.content);
        this.editedContent.set(file.content);
        this.savedBaseline.set(file.content);
        return;
      }

      // ── Same-file reload (SSE or save response) ──────────────────
      // The user has the file open and is editing. If they have no
      // unsaved edits, it's safe to pull in the new on-disk content.
      // If they DO have unsaved edits, leave the editor alone — the
      // incoming push is either the round-trip of our own save
      // (no-op anyway) or an external write the user can decide to
      // reload manually.
      //
      // `untracked` is critical here: the dirty check reads
      // `editedContent` and `savedBaseline`, and the writes below set
      // exactly those signals. Without `untracked`, those reads would
      // subscribe the effect to those signals and the writes would
      // schedule an infinite re-run. We want the effect to react ONLY
      // to `file` (and `currentFilePath`, set only on file switches) —
      // user typing must never trigger the reset.
      if (untracked(() => this.editedContent() === this.savedBaseline())) {
        this.originalContent.set(file.content);
        this.editedContent.set(file.content);
        this.savedBaseline.set(file.content);
      }
    });
  }

  onContentChange(content: string): void {
    this.editedContent.set(content);
  }

  /**
   * Called by `WorkspaceComponent.saveFile()` after the PUT resolves
   * successfully. Aligns the saved-state baseline with the content
   * that was just written so that `isDirty()` returns false and a
   * follow-up SSE push of the same file is allowed to refresh the
   * editor (the round-trip of our own save).
   */
  markSaved(): void {
    const content = this.editedContent();
    this.savedBaseline.set(content);
    this.originalContent.set(content);
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }
}
