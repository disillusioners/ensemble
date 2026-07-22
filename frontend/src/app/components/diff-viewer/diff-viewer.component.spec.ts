import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { DiffViewerComponent } from './diff-viewer.component';
import { WorkspaceService } from '../../services/workspace.service';
import type { GitDiffResponse } from '../../models/workspace.model';

/**
 * Tests for `DiffViewerComponent`.
 *
 * Pattern: Angular `TestBed` with a stubbed `WorkspaceService`. The
 * component renders a status header (badge) above an optional
 * CodeMirror `MergeView`. jsdom can't measure the MergeView gutter
 * properly, so we focus the assertions on the badge / body states
 * that render BEFORE the MergeView is mounted.
 *
 *   - `not_a_git_repo`  → "Not a Git Repo" badge + `.no-git` body
 *   - no changes        → "No Changes" badge + `.no-changes` body
 *   - modified          → "Modified" badge + `.merge-container` slot
 */
describe('DiffViewerComponent', () => {
  let fixture: ComponentFixture<DiffViewerComponent>;
  let component: DiffViewerComponent;
  let mockWorkspace: {
    currentDiff: ReturnType<typeof signal<GitDiffResponse | null>>;
  };

  function makeDiff(overrides: Partial<GitDiffResponse> = {}): GitDiffResponse {
    return {
      project_id: 'project-1',
      path: 'src/main.ts',
      has_changes: true,
      diff: '-old\n+new',
      head_content: 'old',
      working_content: 'new',
      error: null,
      ...overrides,
    };
  }

  beforeEach(async () => {
    mockWorkspace = {
      currentDiff: signal<GitDiffResponse | null>(null),
    };

    await TestBed.configureTestingModule({
      imports: [DiffViewerComponent],
      providers: [{ provide: WorkspaceService, useValue: mockWorkspace }],
    }).compileComponents();

    fixture = TestBed.createComponent(DiffViewerComponent);
    component = fixture.componentInstance;
  });

  // ── 1) Component creation ─────────────────────────────────────

  it('creates successfully', () => {
    expect(component).toBeTruthy();
  });

  // ── 2) Signal mirror ──────────────────────────────────────────

  describe('diff signal', () => {
    it('should be null when the workspace signal is null', () => {
      expect(component.diff()).toBeNull();
    });

    it('should reflect changes to workspace.currentDiff', () => {
      const diff = makeDiff();
      mockWorkspace.currentDiff.set(diff);
      expect(component.diff()).toEqual(diff);

      mockWorkspace.currentDiff.set(null);
      expect(component.diff()).toBeNull();
    });
  });

  // ── 3) DOM rendering: not a git repo ──────────────────────────

  describe('not-a-git-repo state', () => {
    beforeEach(() => {
      mockWorkspace.currentDiff.set(makeDiff({
        error: 'not_a_git_repo',
        has_changes: false,
        diff: null,
        head_content: null,
        working_content: null,
      }));
      fixture.detectChanges();
    });

    it('should render the "Not a Git Repo" badge', () => {
      const badge = fixture.nativeElement.querySelector('.badge') as HTMLElement | null;
      expect(badge?.textContent).toContain('Not a Git Repo');
    });

    it('should NOT render the "No Changes" or "Modified" badges', () => {
      expect(fixture.nativeElement.querySelector('.badge.clean')).toBeNull();
      expect(fixture.nativeElement.querySelector('.badge.modified')).toBeNull();
    });

    it('should render the .no-git body explaining the state', () => {
      const body = fixture.nativeElement.querySelector('.no-git') as HTMLElement | null;
      expect(body?.textContent).toContain('not a git repository');
    });

    it('should NOT render the merge container when there is an error', () => {
      expect(fixture.nativeElement.querySelector('.merge-container')).toBeNull();
    });
  });

  // ── 4) DOM rendering: no changes ──────────────────────────────

  describe('no-changes state', () => {
    beforeEach(() => {
      mockWorkspace.currentDiff.set(makeDiff({
        has_changes: false,
        diff: null,
        head_content: null,
        working_content: null,
        error: null,
      }));
      fixture.detectChanges();
    });

    it('should render the "No Changes" badge with the clean class', () => {
      const badge = fixture.nativeElement.querySelector('.badge.clean') as HTMLElement | null;
      expect(badge?.textContent).toContain('No Changes');
    });

    it('should NOT render the merge container', () => {
      expect(fixture.nativeElement.querySelector('.merge-container')).toBeNull();
    });

    it('should render the .no-changes body', () => {
      const body = fixture.nativeElement.querySelector('.no-changes') as HTMLElement | null;
      expect(body?.textContent).toContain('matches HEAD');
    });

    it('should NOT render the .no-git body', () => {
      expect(fixture.nativeElement.querySelector('.no-git')).toBeNull();
    });
  });

  // ── 5) DOM rendering: modified ────────────────────────────────

  describe('modified state', () => {
    beforeEach(() => {
      mockWorkspace.currentDiff.set(makeDiff({
        has_changes: true,
        diff: '-old\n+new',
        head_content: 'old',
        working_content: 'new',
        error: null,
      }));
      fixture.detectChanges();
    });

    it('should render the "Modified" badge', () => {
      const badge = fixture.nativeElement.querySelector('.badge.modified') as HTMLElement | null;
      expect(badge?.textContent).toContain('Modified');
    });

    it('should render the merge container slot for the CodeMirror MergeView', () => {
      expect(fixture.nativeElement.querySelector('.merge-container')).toBeTruthy();
    });

    it('should NOT render the .no-changes or .no-git bodies', () => {
      expect(fixture.nativeElement.querySelector('.no-changes')).toBeNull();
      expect(fixture.nativeElement.querySelector('.no-git')).toBeNull();
    });
  });

  // ── 6) Error priority over has_changes ────────────────────────

  describe('error priority', () => {
    it('should render the "Not a Git Repo" badge when error is set even if has_changes is true', () => {
      mockWorkspace.currentDiff.set(makeDiff({
        has_changes: true,
        error: 'not_a_git_repo',
      }));
      fixture.detectChanges();

      const badge = fixture.nativeElement.querySelector('.badge') as HTMLElement | null;
      expect(badge?.textContent).toContain('Not a Git Repo');
      expect(fixture.nativeElement.querySelector('.badge.modified')).toBeNull();
    });
  });

  // ── 7) DOM rendering: empty state ─────────────────────────────

  describe('empty state', () => {
    it('should render nothing when diff() is null', () => {
      expect(fixture.nativeElement.querySelector('.diff-viewer')).toBeNull();
    });
  });

  // ── 8) Filepath header ────────────────────────────────────────

  describe('header', () => {
    it('should render the file path in the header', () => {
      mockWorkspace.currentDiff.set(makeDiff({ path: 'src/foo.py' }));
      fixture.detectChanges();

      const header = fixture.nativeElement.querySelector('.filepath') as HTMLElement | null;
      expect(header?.textContent).toContain('src/foo.py');
    });
  });
});