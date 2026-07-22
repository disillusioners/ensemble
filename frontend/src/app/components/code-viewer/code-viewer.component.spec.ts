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
 *   - Badge visibility (Binary / Truncated)
 */
describe('CodeViewerComponent', () => {
  let fixture: ComponentFixture<CodeViewerComponent>;
  let component: CodeViewerComponent;
  let mockWorkspace: {
    currentFile: ReturnType<typeof signal<FileContentResponse | null>>;
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

  beforeEach(async () => {
    mockWorkspace = {
      currentFile: signal<FileContentResponse | null>(null),
    };

    await TestBed.configureTestingModule({
      imports: [CodeViewerComponent],
      providers: [{ provide: WorkspaceService, useValue: mockWorkspace }],
    }).compileComponents();

    fixture = TestBed.createComponent(CodeViewerComponent);
    component = fixture.componentInstance;
  });

  // ── 1) formatSize (pure method on real component) ────────────

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

    it('should render the file path in the header', () => {
      const header = fixture.nativeElement.querySelector('.filepath') as HTMLElement | null;
      expect(header?.textContent).toContain('test.py');
    });

    it('should render the file size and line count in the meta line', () => {
      const meta = fixture.nativeElement.querySelector('.meta') as HTMLElement | null;
      expect(meta?.textContent).toContain('1 lines');
      expect(meta?.textContent).toContain('16 B');
    });

    it('should render the code content area when file is not binary', () => {
      expect(fixture.nativeElement.querySelector('.code-content')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('.binary-placeholder')).toBeNull();
    });

    it('should NOT show the Binary badge for text files', () => {
      expect(fixture.nativeElement.querySelector('.badge.binary')).toBeNull();
    });

    it('should NOT show the Truncated badge when the file is fully loaded', () => {
      expect(fixture.nativeElement.querySelector('.badge.truncated')).toBeNull();
    });
  });

  // ── 4) DOM rendering: binary file ──────────────────────────────

  describe('DOM rendering (binary file)', () => {
    it('should show the Binary badge', () => {
      mockWorkspace.currentFile.set(makeFile({
        path: 'image.png',
        content: '',
        language: null,
        total_lines: 0,
        binary: true,
        size_bytes: 1024,
      }));
      fixture.detectChanges();

      const badge = fixture.nativeElement.querySelector('.badge.binary') as HTMLElement | null;
      expect(badge?.textContent).toContain('Binary');
    });

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
    it('should show the Truncated badge', () => {
      mockWorkspace.currentFile.set(makeFile({
        path: 'big.txt',
        content: 'partial contents…',
        language: null,
        total_lines: 1000,
        truncated: true,
        size_bytes: 1_048_576,
      }));
      fixture.detectChanges();

      const badge = fixture.nativeElement.querySelector('.badge.truncated') as HTMLElement | null;
      expect(badge?.textContent).toContain('Truncated');
    });

    it('should still render the code content area (text files are never replaced)', () => {
      mockWorkspace.currentFile.set(makeFile({
        path: 'big.txt',
        content: 'partial contents…',
        language: 'plaintext',
        total_lines: 1000,
        truncated: true,
        size_bytes: 1_048_576,
      }));
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.code-content')).toBeTruthy();
    });
  });

  // ── 6) DOM rendering: empty state ──────────────────────────────

  describe('DOM rendering (no file selected)', () => {
    it('should render nothing when file() is null', () => {
      expect(fixture.nativeElement.querySelector('.code-viewer')).toBeNull();
    });
  });
});