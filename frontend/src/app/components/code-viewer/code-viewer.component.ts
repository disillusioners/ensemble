import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceService } from '../../services/workspace.service';
import { CodemirrorDirective } from './codemirror.directive';

/**
 * Per-path edit state stored in the map when a file is not the active
 * tab. Switching tabs preserves unsaved edits because the editor's
 * signals are saved to the map under the outgoing path and re-loaded
 * from the map when the user re-activates that tab.
 */
interface EditState {
  original: string;
  edited: string;
  baseline: string;
}

/**
 * Code viewer — multi-file aware.
 *
 * The viewer holds THREE active-file signals (`originalContent`,
 * `editedContent`, `savedBaseline`) that mirror the editor buffer for
 * the currently active tab. A per-path `editStateMap` keeps the buffer
 * state for every OTHER open tab, so switching tabs preserves unsaved
 * edits and round-trips back to the original after a save.
 *
 * The effect below reacts to `workspace.currentFile()` (which is now a
 * computed derived from `_activeFilePath`). When the path changes:
 *
 *   1. If a previous file is active, snapshot its `{original, edited,
 *      baseline}` triple into the map under the previous path.
 *   2. Load the new path's state from the map. If absent, seed it from
 *      `file.content` and record the seed in the map so a future
 *      switch-back sees the clean baseline.
 *   3. On a SAME-path reload (SSE push, save response), the
 *      `savedBaseline`-gate decides whether to accept the new content
 *      or leave the user's edits untouched (F1/F2).
 *
 * The effect MUST read `currentFilePath()` unconditionally before any
 * if-branch — see the "Angular Effect Dependency Tracking Hazard"
 * project-history note: an effect that only reads signals inside some
 * branches can lose subscriptions on paths that skip the read.
 *
 * All signal writes in this effect are guarded with equality checks
 * (`if (signal() !== newValue) signal.set(newValue)`). Without the
 * guards, an effect that writes to a signal schedules itself to
 * re-run on the next microtask, which then writes the same value
 * again, ad infinitum — a silent infinite loop that hangs the
 * scheduler until test timeout.
 *
 * Binary and truncated files get a separate guard layer (F3/F4): the
 * effect clears the buffer to '' so a stray Save cannot write the
 * previous text file's content over a binary or truncated file. The
 * `canSave()` belt-and-suspenders guard lives in WorkspaceComponent.
 */
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
               [content]="editedContent()"
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
  // `currentFile` is now a computed signal on the service (derived
  // from `_activeFilePath` + the per-path content cache). Computed
  // signals are already readonly, so we re-expose it directly —
  // calling `.asReadonly()` on `Signal<T>` is a type error.
  public readonly file = this.workspace.currentFile;
  public readonly editedContent = signal<string>('');
  public readonly isDirty = computed(() => this.editedContent() !== this.originalContent());
  public readonly currentContent = this.editedContent.asReadonly();

  /**
   * The path of the file currently bound to the active signals. Tracked
   * by the effect so subsequent changes to `file()` can be classified as
   * "same path" (SSE round-trip / save response) or "different path"
   * (tab switch). Without this, switching to another tab would clobber
   * unsaved edits because the effect cannot tell the two cases apart
   * from `currentFile` alone.
   */
  private readonly currentFilePath = signal<string | null>(null);

  /**
   * The content the editor last agreed was "on disk" — either the
   * freshly loaded file content or the content of the last successful
   * save. Compared against `editedContent` to decide whether a same-path
   * reload is safe to apply: if the user has unsaved edits, the
   * incoming reload is ignored to preserve keystrokes that arrived
   * after Save but before the SSE push or save response.
   */
  private readonly savedBaseline = signal<string>('');

  /**
   * Per-path edit-state store for INACTIVE tabs. Entries are written
   * when a tab loses focus and read when a tab regains focus. The map
   * only grows — paths are kept across the session so a long-running
   * workflow can edit many files in turn without losing state.
   */
  private readonly editStateMap = new Map<string, EditState>();

  constructor() {
    effect(() => {
      const file = this.file();

      // ── Dependency-tracking hazard guard ────────────────────────
      // Read `currentFilePath()` UNCONDITIONALLY before any if-branch
      // so the effect always subscribes to it. Skipping this read in
      // some branches (e.g. when `file` is null) has been a recurring
      // source of "the workspace appears stuck after visiting the All
      // tab" bugs in this codebase.
      const previousPath = this.currentFilePath();

      // ── Guard 1: no file selected ────────────────────────────────
      // The viewer renders nothing when `file()` is null (template @if
      // skips the block), but we still want every signal to be in a
      // well-defined empty state and the map cleared so stale edits
      // never bleed across selections.
      if (!file) {
        this.editStateMap.clear();
        this.setOriginal('');
        this.setEdited('');
        this.setBaseline('');
        this.setCurrentPath(null);
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
        this.setOriginal('');
        this.setEdited('');
        this.setBaseline('');
        this.setCurrentPath(file.path);
        // Binary/truncated files have no editable content so they can
        // never be dirty. The dirty flag will be cleared on the next
        // user-driven onContentChange/markSaved cycle (which never
        // fires for read-only files), or on the next effect run for
        // a different file. We deliberately do NOT call
        // `workspace.setFileDirty` here because writing to a service
        // signal from inside an effect schedules the effect to re-run
        // via the microtask scheduler — see the "infinite effect loop"
        // note in the constructor docstring.
        return;
      }

      const incomingPath = file.path;

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
      if (incomingPath === previousPath) {
        if (untracked(() => this.editedContent() === this.savedBaseline())) {
          this.setOriginal(file.content);
          this.setEdited(file.content);
          this.setBaseline(file.content);
          this.editStateMap.set(incomingPath, {
            original: file.content,
            edited: file.content,
            baseline: file.content,
          });
          // Fresh content is by definition clean. The dirty flag will
          // be cleared by markSaved() / onContentChange() — calling
          // `workspace.setFileDirty` from the effect would schedule
          // the effect to re-run on the next microtask, causing an
          // infinite loop. See the "infinite effect loop" note in
          // the constructor docstring.
        }
        return;
      }

      // ── Different file: snapshot outgoing, load incoming ────────
      // Save the currently-active file's state to the map under the
      // outgoing path so a switch back preserves unsaved edits. Read
      // via `untracked` to avoid re-subscribing the effect to the
      // active signals (those writes below would otherwise cause an
      // infinite re-run).
      if (previousPath && previousPath !== incomingPath) {
        const outgoing = untracked<EditState>(() => ({
          original: this.originalContent(),
          edited: this.editedContent(),
          baseline: this.savedBaseline(),
        }));
        this.editStateMap.set(previousPath, outgoing);
      }

      const stored = this.editStateMap.get(incomingPath);
      if (stored) {
        this.setOriginal(stored.original);
        this.setEdited(stored.edited);
        this.setBaseline(stored.baseline);
      } else {
        this.setOriginal(file.content);
        this.setEdited(file.content);
        this.setBaseline(file.content);
        this.editStateMap.set(incomingPath, {
          original: file.content,
          edited: file.content,
          baseline: file.content,
        });
        // A freshly-loaded file is clean by definition. The dirty
        // flag will be cleared by markSaved() / onContentChange() —
        // calling `workspace.setFileDirty` from the effect would
        // schedule the effect to re-run on the next microtask,
        // causing an infinite loop. See the "infinite effect loop"
        // note in the constructor docstring.
      }

      this.setCurrentPath(incomingPath);
    });
  }

  /**
   * Internal write helpers — each guards against writing the same
   * value back, since effects that write to signals schedule
   * themselves to re-run. Without these guards, the effect re-runs
   * every microtask with the same data, producing a silent infinite
   * loop that only surfaces as a test timeout.
   */
  private setOriginal(value: string): void {
    if (this.originalContent() !== value) this.originalContent.set(value);
  }
  private setEdited(value: string): void {
    if (this.editedContent() !== value) this.editedContent.set(value);
  }
  private setBaseline(value: string): void {
    if (this.savedBaseline() !== value) this.savedBaseline.set(value);
  }
  private setCurrentPath(value: string | null): void {
    if (this.currentFilePath() !== value) this.currentFilePath.set(value);
  }

  onContentChange(content: string): void {
    this.setEdited(content);
    // Update the map so a tab switch preserves the edit. The map
    // mirrors the active signals for the current path; on switch-back
    // we restore from the map.
    const path = untracked(() => this.currentFilePath());
    if (!path) return;
    const baseline = untracked(() => this.savedBaseline());
    const original = untracked(() => this.originalContent());
    this.editStateMap.set(path, { original, edited: content, baseline });
    // Mirror dirty state to the service so the tab indicator updates.
    // Dirty means `content !== original` (the pristine on-disk version),
    // NOT `content !== savedBaseline` — we want the dirty dot to
    // appear the moment the user diverges from the file's loaded
    // content, not only after an explicit "mark clean" boundary.
    this.workspace.setFileDirty(path, content !== original);
  }

  /**
   * Called by `WorkspaceComponent.saveFile()` after the PUT resolves
   * successfully. Aligns the saved-state baseline with the content
   * that was just written so that `isDirty()` returns false and a
   * follow-up SSE push of the same file is allowed to refresh the
   * editor (the round-trip of our own save). Also mirrors the new
   * clean state to the service so the tab's dirty dot clears.
   *
   * `savedContent` is the body that was actually sent in the PUT. We
   * align the baseline to THAT, not to the live `editedContent()`,
   * because the user may have typed more characters between the PUT
   * departing and the response arriving. If they did, `editedContent
   * !== savedContent`, and the file should remain dirty — the disk
   * holds the older PUT body, so isDirty must stay true. We use
   * `untracked()` for signal reads inside this handler to avoid
   * creating unwanted effect dependencies (markSaved is called from
   * event handlers, not effects, but the read safety is cheap).
   */
  markSaved(savedContent: string): void {
    const path = untracked(() => this.currentFilePath());
    this.setBaseline(savedContent);

    // Only update originalContent if the edited content still matches
    // what was saved. If the user typed more after the PUT departed,
    // editedContent !== savedContent, so the file remains correctly
    // dirty.
    if (untracked(() => this.editedContent()) === savedContent) {
      this.setOriginal(savedContent);
    }
    // If editedContent !== savedContent, the original stays as the
    // pre-save version, so isDirty remains true — correct behavior.

    if (path) {
      // Update the map entry.
      this.editStateMap.set(path, {
        original: untracked(() => this.originalContent()),
        edited: untracked(() => this.editedContent()),
        baseline: savedContent,
      });
      this.workspace.setFileDirty(
        path,
        untracked(() => this.editedContent()) !== untracked(() => this.originalContent())
      );
    }
  }

  /**
   * Remove a path's entry from the per-path edit-state map. Called by
   * `WorkspaceComponent.onTabClose(path)` when a tab is closed so the
   * map does not retain stale state for a re-opened tab and does not
   * grow without bound across the session.
   */
  forgetTab(path: string): void {
    this.editStateMap.delete(path);
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }
}
