import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { CodeViewerComponent } from './code-viewer.component';
import { WorkspaceService } from '../../services/workspace.service';
import type { FileContentResponse } from '../../models/workspace.model';

/**
 * Tests for `CodeViewerComponent`.
 *
 * Pattern: Angular `TestBed` with a stubbed `WorkspaceService`. The
 * component is small, signal-driven, and bound to the live
 * `WorkspaceService.currentFile` signal — TestBed with a fake signal
 * gives us full coverage of:
 *   - formatSize (pure method on the REAL component)
 *   - DOM rendering for text files (with the live CodeMirror directive)
 *   - DOM rendering for binary files
 *   - DOM rendering for truncated files
 *
 * The mock WorkspaceService now exposes the multi-file tab API
 * (`setFileDirty`, `setActiveFile`) the component calls in its effect
 * and event handlers. Without these, the effect throws on every file
 * change.
 *
 * Note: badges (Binary / Truncated) and file metadata moved to the
 * workspace toolbar in the unified-toolbar refactor, so their rendering
 * is verified at the workspace level instead.
 */
describe('CodeViewerComponent', () => {
  let fixture: ComponentFixture<CodeViewerComponent>;
  let component: CodeViewerComponent;
  let mockWorkspace: {
    currentFile: ReturnType<typeof signal<FileContentResponse | null>>;
    activeFilePath: ReturnType<typeof signal<string | null>>;
    setFileDirty: jest.Mock;
    setActiveFile: jest.Mock;
    openFile: jest.Mock;
  };

  function makeFile(overrides: Partial<FileContentResponse> = {}): FileContentResponse {
    return {
      project_id: 'project-1',
      path: 'src/main.ts',
      content: 'const value = 1;',
      language: 'typescript',
      total_lines: 1,
      offset: 0,
      limit: 1000,
      truncated: false,
      binary: false,
      size_bytes: 16,
      ...overrides,
    };
  }

  /**
   * Drive the component's view of "the current file" by setting both
   * `currentFile` and `activeFilePath`. In production, the service
   * computes `currentFile` from `activeFilePath` + the per-path content
   * cache; the mock does not, so we keep them in sync explicitly here.
   */
  function setActiveFile(file: FileContentResponse | null): void {
    mockWorkspace.currentFile.set(file);
    mockWorkspace.activeFilePath.set(file?.path ?? null);
  }

  beforeEach(async () => {
    mockWorkspace = {
      currentFile: signal<FileContentResponse | null>(null),
      activeFilePath: signal<string | null>(null),
      // Multi-file tab API the component calls in its effect and
      // event handlers. Without these the effect throws on every
      // file change.
      setFileDirty: jest.fn(),
      setActiveFile: jest.fn().mockReturnValue(true),
      openFile: jest.fn(),
    };

    await TestBed.configureTestingModule({
      imports: [CodeViewerComponent],
      providers: [{ provide: WorkspaceService, useValue: mockWorkspace }],
    }).compileComponents();

    fixture = TestBed.createComponent(CodeViewerComponent);
    component = fixture.componentInstance;
  });  // ── 1) formatSize (pure method on real component) ────────────

  describe('formatSize', () => {
    it('should format byte sizes below 1024 as integer bytes', () => {
      expect(component.formatSize(512)).toBe('512 B');
    });

    it('should format exactly 1024 bytes as 1.0 KB', () => {
      expect(component.formatSize(1024)).toBe('1.0 KB');
    });

    it('should format 1536 bytes as 1.5 KB', () => {
      expect(component.formatSize(1536)).toBe('1.5 KB');
    });

    it('should format exactly 1048576 bytes as 1.0 MB', () => {
      expect(component.formatSize(1_048_576)).toBe('1.0 MB');
    });

    it('should format 2621440 bytes as 2.5 MB', () => {
      expect(component.formatSize(2_621_440)).toBe('2.5 MB');
    });

    it('should format zero as 0 B', () => {
      expect(component.formatSize(0)).toBe('0 B');
    });
  });

  // ── 2) Signal mirror (file() exposes workspace.currentFile) ────

  describe('file signal', () => {
    it('should be null when the workspace signal is null', () => {
      expect(component.file()).toBeNull();
    });

    it('should reflect changes to workspace.currentFile', () => {
      const file = makeFile();
      mockWorkspace.currentFile.set(file);
      expect(component.file()).toEqual(file);

      const updated = makeFile({ path: 'src/updated.ts' });
      mockWorkspace.currentFile.set(updated);
      expect(component.file()).toEqual(updated);
    });
  });

  // ── 3) DOM rendering: text file ────────────────────────────────

  describe('DOM rendering (text file)', () => {
    beforeEach(() => {
      mockWorkspace.currentFile.set(makeFile({ path: 'test.py', language: 'python' }));
      fixture.detectChanges();
    });

    it('should render the code content area when file is not binary', () => {
      expect(fixture.nativeElement.querySelector('.code-content')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('.binary-placeholder')).toBeNull();
    });
  });

  // ── 4) DOM rendering: binary file ──────────────────────────────

  describe('DOM rendering (binary file)', () => {
    it('should render the binary placeholder instead of the code area', () => {
      mockWorkspace.currentFile.set(makeFile({
        path: 'image.png',
        content: '',
        language: null,
        total_lines: 0,
        binary: true,
        size_bytes: 1024,
      }));
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.binary-placeholder')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('.code-content')).toBeNull();
    });

    it('should include the formatted size in the binary placeholder', () => {
      mockWorkspace.currentFile.set(makeFile({
        path: 'image.png',
        content: '',
        language: null,
        total_lines: 0,
        binary: true,
        size_bytes: 2_621_440,
      }));
      fixture.detectChanges();

      const placeholder = fixture.nativeElement.querySelector('.binary-placeholder') as HTMLElement | null;
      expect(placeholder?.textContent).toContain('2.5 MB');
    });
  });

  // ── 5) DOM rendering: truncated file ───────────────────────────

  describe('DOM rendering (truncated file)', () => {
    it('should render the truncated placeholder instead of the code editor (F4)', () => {
      // F4 — a truncated preview is never editable; the editor must
      // show a read-only placeholder so the user cannot accidentally
      // edit the preview and overwrite the full file on save.
      mockWorkspace.currentFile.set(makeFile({
        path: 'big.txt',
        content: 'partial contents…',
        language: 'plaintext',
        total_lines: 1000,
        truncated: true,
        size_bytes: 1_048_576,
      }));
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.truncated-placeholder')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('.code-content')).toBeNull();
    });
  });

  // ── 6) DOM rendering: empty state ──────────────────────────────

  describe('DOM rendering (no file selected)', () => {
    it('should render nothing when file() is null', () => {
      expect(fixture.nativeElement.querySelector('.code-viewer')).toBeNull();
    });
  });

  // ── 7) Edit mode: dirty state tracking ─────────────────────────

  describe('edit mode', () => {
    beforeEach(() => {
      mockWorkspace.currentFile.set(makeFile({ content: 'original' }));
      fixture.detectChanges();
    });

    it('should start not dirty after file load', () => {
      expect(component.isDirty()).toBe(false);
      expect(component.editedContent()).toBe('original');
    });

    it('should expose the current content as a public readonly signal', () => {
      expect(component.currentContent()).toBe('original');
    });

    it('should mark dirty when content changes via onContentChange', () => {
      component.onContentChange('edited');
      expect(component.isDirty()).toBe(true);
      expect(component.editedContent()).toBe('edited');
      expect(component.currentContent()).toBe('edited');
    });

    it('should clear dirty when content returns to original', () => {
      component.onContentChange('edited');
      expect(component.isDirty()).toBe(true);
      component.onContentChange('original');
      expect(component.isDirty()).toBe(false);
    });

    it('should reset editedContent to new file content when file changes', () => {
      component.onContentChange('edited');
      expect(component.isDirty()).toBe(true);

      mockWorkspace.currentFile.set(makeFile({ path: 'src/other.ts', content: 'fresh content' }));
      fixture.detectChanges();

      expect(component.editedContent()).toBe('fresh content');
      expect(component.currentContent()).toBe('fresh content');
      expect(component.isDirty()).toBe(false);
    });

    it('should bind [editable]="true" to the codemirror directive in the template', () => {
      const codeContent = fixture.nativeElement.querySelector('.code-content') as HTMLElement | null;
      expect(codeContent).toBeTruthy();
      const cmContent = codeContent?.querySelector('.cm-content') as HTMLElement | null;
      expect(cmContent?.getAttribute('contenteditable')).toBe('true');
    });

    // ── F1/F2: same-file reload (SSE / save response) must NOT clobber
    //    unsaved edits. The effect gates the reset on `editedContent ===
    //    savedBaseline`; only when there are no unsaved edits does it
    //    pull in the new on-disk content.
    it('should preserve unsaved edits when the SAME file is reloaded (SSE push)', () => {
      component.onContentChange('edited');
      expect(component.isDirty()).toBe(true);

      // Same path (`src/main.ts`), fresh content from disk — the SSE
      // round-trip of an external write must not stomp the keystrokes
      // typed after the in-flight save was sent.
      mockWorkspace.currentFile.set(
        makeFile({ path: 'src/main.ts', content: 'fresh-from-disk' })
      );
      fixture.detectChanges();

      expect(component.editedContent()).toBe('edited');
      expect(component.currentContent()).toBe('edited');
      expect(component.isDirty()).toBe(true);
    });

    it('should accept the new content when the SAME file is reloaded with no unsaved edits', () => {
      // Not dirty — saved baseline equals edited content. Safe to pull
      // in the latest on-disk content (e.g. user reverted their edits
      // and an SSE push arrives).
      expect(component.isDirty()).toBe(false);

      mockWorkspace.currentFile.set(
        makeFile({ path: 'src/main.ts', content: 'fresh-from-disk' })
      );
      fixture.detectChanges();

      expect(component.editedContent()).toBe('fresh-from-disk');
      expect(component.currentContent()).toBe('fresh-from-disk');
      expect(component.isDirty()).toBe(false);
    });

    // ── F2: `markSaved()` aligns the saved-state baseline with the
    //    currently-edited content so a follow-up SSE push (the round-trip
    //    of our own save) is allowed to refresh the editor.
    it('markSaved() should make isDirty false without changing editedContent', () => {
      component.onContentChange('edited');
      expect(component.isDirty()).toBe(true);

      component.markSaved();

      expect(component.editedContent()).toBe('edited');
      expect(component.currentContent()).toBe('edited');
      expect(component.isDirty()).toBe(false);
    });

    it('after markSaved(), a same-file reload with new content is accepted', () => {
      component.onContentChange('edited');
      component.markSaved();
      expect(component.isDirty()).toBe(false);

      // The round-trip of our own save lands — same path, fresh content.
      // Now that the baseline matches the editor, the gated effect
      // accepts the refresh.
      mockWorkspace.currentFile.set(
        makeFile({ path: 'src/main.ts', content: 'edited' })
      );
      fixture.detectChanges();

      expect(component.editedContent()).toBe('edited');
      expect(component.isDirty()).toBe(false);
    });
  });

  // ── 8) F3: binary file read-only guards ─────────────────────────

  describe('binary file guards (F3)', () => {
    it('should reset editedContent to empty when switching from a text file to a binary file', () => {
      // Seed with a text file and dirty edits.
      mockWorkspace.currentFile.set(makeFile({ content: 'original' }));
      fixture.detectChanges();
      component.onContentChange('user typing');
      expect(component.isDirty()).toBe(true);

      // Switch to a binary file — the editor buffer must NOT carry the
      // previous text file's content forward, otherwise Save would
      // overwrite the binary with stale text.
      mockWorkspace.currentFile.set(
        makeFile({
          path: 'image.png',
          content: '',
          language: null,
          total_lines: 0,
          binary: true,
          size_bytes: 1024,
        })
      );
      fixture.detectChanges();

      expect(component.editedContent()).toBe('');
      expect(component.currentContent()).toBe('');
      expect(component.isDirty()).toBe(false);
    });

    it('should render the binary placeholder instead of the code editor', () => {
      mockWorkspace.currentFile.set(
        makeFile({
          path: 'image.png',
          content: '',
          language: null,
          total_lines: 0,
          binary: true,
          size_bytes: 1024,
        })
      );
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.binary-placeholder')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('.code-content')).toBeNull();
    });
  });

  // ── 9) F4: truncated file read-only guards ──────────────────────

  describe('truncated file guards (F4)', () => {
    it('should reset editedContent to empty when switching from a text file to a truncated file', () => {
      mockWorkspace.currentFile.set(makeFile({ content: 'original' }));
      fixture.detectChanges();
      component.onContentChange('user typing');
      expect(component.isDirty()).toBe(true);

      // Switch to a truncated preview — never seed the editor with the
      // truncated slice, otherwise Save would destroy the file by
      // writing only the preview slice to disk.
      mockWorkspace.currentFile.set(
        makeFile({
          path: 'big.txt',
          content: 'partial contents…',
          language: 'plaintext',
          total_lines: 1000,
          truncated: true,
          size_bytes: 1_048_576,
        })
      );
      fixture.detectChanges();

      expect(component.editedContent()).toBe('');
      expect(component.currentContent()).toBe('');
      expect(component.isDirty()).toBe(false);
    });

    it('should render the truncated placeholder instead of the code editor', () => {
      mockWorkspace.currentFile.set(
        makeFile({
          path: 'big.txt',
          content: 'partial contents…',
          language: 'plaintext',
          total_lines: 1000,
          truncated: true,
          size_bytes: 1_048_576,
        })
      );
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.truncated-placeholder')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('.code-content')).toBeNull();
    });
  });

  // ── 10) Multi-file tab state: per-path edit-state map ─────────────
  // Round-trip A → B → A must preserve A's unsaved edits when the user
  // returns to it. The map is keyed by file path and updated whenever a
  // file loses focus; on reactivation the editor's signals are restored
  // from the map.

  describe('per-path edit-state map', () => {
    /**
     * Drive the component's view of "the current file" by setting both
     * `currentFile` and `activeFilePath`. In production, the service
     * computes `currentFile` from `activeFilePath` + the per-path
     * content cache; the mock does not, so we keep them in sync
     * explicitly here.
     */
    function switchTo(path: string, content: string): void {
      const file = makeFile({ path, content });
      mockWorkspace.currentFile.set(file);
      mockWorkspace.activeFilePath.set(path);
      fixture.detectChanges();
    }

    it('preserves unsaved edits when switching from file A to file B and back', () => {
      // Open file A and dirty it.
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edited');
      expect(component.isDirty()).toBe(true);
      expect(component.editedContent()).toBe('a-edited');

      // Switch to file B — A's edits must be saved to the map and B
      // loads cleanly.
      switchTo('b.ts', 'b-original');
      expect(component.editedContent()).toBe('b-original');
      expect(component.isDirty()).toBe(false);

      // Edit file B.
      component.onContentChange('b-edited');
      expect(component.editedContent()).toBe('b-edited');
      expect(component.isDirty()).toBe(true);

      // Switch back to A — A's edits must be restored.
      switchTo('a.ts', 'a-original');
      expect(component.editedContent()).toBe('a-edited');
      expect(component.isDirty()).toBe(true);
    });

    it('preserves unsaved edits across A → B → C → A round-trip', () => {
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edit-1');
      switchTo('b.ts', 'b-original');
      component.onContentChange('b-edit-1');
      switchTo('c.ts', 'c-original');
      expect(component.editedContent()).toBe('c-original');

      // Return to A — original edit must still be there.
      switchTo('a.ts', 'a-original');
      expect(component.editedContent()).toBe('a-edit-1');
      expect(component.isDirty()).toBe(true);
    });

    it('does not mark a file dirty just from switching tabs to it', () => {
      // Open and dirty file A.
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edited');

      // Switch to B (clean) — the effect must NOT call setFileDirty
      // (we removed those calls from the effect to break an infinite
      // loop; dirty clearing is now driven by onContentChange /
      // markSaved only).
      mockWorkspace.setFileDirty.mockClear();
      switchTo('b.ts', 'b-original');
      expect(mockWorkspace.setFileDirty).not.toHaveBeenCalled();

      // Edit B — onContentChange should mark B dirty in the service.
      mockWorkspace.setFileDirty.mockClear();
      component.onContentChange('b-edited');
      expect(mockWorkspace.setFileDirty).toHaveBeenLastCalledWith(
        'b.ts',
        true
      );

      // Switch back to A — the effect must NOT call setFileDirty
      // even though A is being re-activated. A must still be dirty.
      mockWorkspace.setFileDirty.mockClear();
      switchTo('a.ts', 'a-original');
      expect(mockWorkspace.setFileDirty).not.toHaveBeenCalled();
      expect(component.isDirty()).toBe(true);
    });

    it('reflects dirty state through onContentChange to setFileDirty', () => {
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edited');
      expect(mockWorkspace.setFileDirty).toHaveBeenCalledWith('a.ts', true);

      mockWorkspace.setFileDirty.mockClear();
      component.onContentChange('a-original');
      expect(mockWorkspace.setFileDirty).toHaveBeenCalledWith('a.ts', false);
    });

    it('clears dirty flag in the service when markSaved() is called', () => {
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edited');
      mockWorkspace.setFileDirty.mockClear();

      component.markSaved();
      expect(mockWorkspace.setFileDirty).toHaveBeenCalledWith('a.ts', false);
      expect(component.isDirty()).toBe(false);

      // After markSaved, a same-file reload with new content should be
      // accepted (savedBaseline === editedContent, so the gate passes).
      switchTo('a.ts', 'a-edited');
      expect(component.editedContent()).toBe('a-edited');
    });

    it('preserves other files dirty state when markSaved() is called on the active file', () => {
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edited');
      switchTo('b.ts', 'b-original');
      component.onContentChange('b-edited');

      mockWorkspace.setFileDirty.mockClear();
      component.markSaved();
      // Only the active file (b.ts) is cleared; A's dirty flag is
      // untouched. The user must explicitly switch to A and save it
      // to clear A's dirty state.
      expect(mockWorkspace.setFileDirty).toHaveBeenCalledWith('b.ts', false);
      expect(mockWorkspace.setFileDirty).not.toHaveBeenCalledWith(
        'a.ts',
        expect.anything()
      );
    });

    it('clears all edit state when switching to no file (null)', () => {
      switchTo('a.ts', 'a-original');
      component.onContentChange('a-edited');
      expect(component.isDirty()).toBe(true);

      // Go to no file — the map is cleared so a fresh start has no
      // stale edits hanging around.
      mockWorkspace.currentFile.set(null);
      mockWorkspace.activeFilePath.set(null);
      fixture.detectChanges();

      expect(component.editedContent()).toBe('');
      expect(component.isDirty()).toBe(false);

      // Re-open A — it should load fresh (clean), not the dirty state
      // from before the null transition.
      switchTo('a.ts', 'a-original');
      expect(component.editedContent()).toBe('a-original');
      expect(component.isDirty()).toBe(false);
    });
  });
});
