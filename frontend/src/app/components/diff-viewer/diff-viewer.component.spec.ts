import { signal } from '@angular/core';
import type { GitDiffResponse } from '../../models/workspace.model';

type Badge = {
  key: 'not_a_git_repo' | 'no_changes' | 'modified';
  text: string;
  className: string;
};

function getBadge(diff: GitDiffResponse): Badge {
  if (diff.error === 'not_a_git_repo') {
    return { key: 'not_a_git_repo', text: 'Not a Git Repo', className: 'badge' };
  }
  if (!diff.has_changes) {
    return { key: 'no_changes', text: 'No Changes', className: 'badge clean' };
  }
  return { key: 'modified', text: 'Modified', className: 'badge modified' };
}

function shouldRenderMerge(
  diff: GitDiffResponse | null,
  containerExists: boolean
): boolean {
  return Boolean(diff?.has_changes && !diff.error && containerExists);
}

class MockWorkspaceService {
  readonly currentDiff = signal<GitDiffResponse | null>(null);
}

type DestroyableMergeView = { destroy: jest.Mock<void, []> };

class TestableDiffViewerComponent {
  public readonly diff;
  public containerExists = false;
  public mergeView: DestroyableMergeView | null = null;
  public readonly createMergeView = jest.fn<DestroyableMergeView, []>(() => ({
    destroy: jest.fn<void, []>(),
  }));

  constructor(workspace: MockWorkspaceService) {
    this.diff = workspace.currentDiff.asReadonly();
  }

  renderDiff(): void {
    if (!shouldRenderMerge(this.diff(), this.containerExists)) return;

    this.mergeView?.destroy();
    this.mergeView = this.createMergeView();
  }

  ngOnDestroy(): void {
    this.mergeView?.destroy();
  }
}

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

describe('DiffViewerComponent logic', () => {
  let workspace: MockWorkspaceService;
  let component: TestableDiffViewerComponent;

  beforeEach(() => {
    workspace = new MockWorkspaceService();
    component = new TestableDiffViewerComponent(workspace);
  });

  it('should mirror workspace.currentDiff', () => {
    const diff = makeDiff();

    workspace.currentDiff.set(diff);
    expect(component.diff()).toEqual(diff);

    workspace.currentDiff.set(null);
    expect(component.diff()).toBeNull();
  });

  it("should return the 'not_a_git_repo' badge for that error", () => {
    expect(getBadge(makeDiff({ error: 'not_a_git_repo' }))).toEqual({
      key: 'not_a_git_repo',
      text: 'Not a Git Repo',
      className: 'badge',
    });
  });

  it("should return the 'no_changes' badge when there are no changes or errors", () => {
    expect(getBadge(makeDiff({ has_changes: false, error: null }))).toEqual({
      key: 'no_changes',
      text: 'No Changes',
      className: 'badge clean',
    });
  });

  it("should return the 'modified' badge when changes exist without an error", () => {
    expect(getBadge(makeDiff({ has_changes: true, error: null }))).toEqual({
      key: 'modified',
      text: 'Modified',
      className: 'badge modified',
    });
  });

  it('should prioritize the git error badge over has_changes', () => {
    const badge = getBadge(makeDiff({ has_changes: true, error: 'not_a_git_repo' }));

    expect(badge.key).toBe('not_a_git_repo');
  });

  it('should not create MergeView when there are no changes', () => {
    workspace.currentDiff.set(makeDiff({ has_changes: false }));
    component.containerExists = true;

    component.renderDiff();

    expect(component.createMergeView).not.toHaveBeenCalled();
  });

  it('should not create MergeView when an error is set', () => {
    workspace.currentDiff.set(makeDiff({ error: 'not_a_git_repo' }));
    component.containerExists = true;

    component.renderDiff();

    expect(component.createMergeView).not.toHaveBeenCalled();
  });

  it('should create MergeView only with changes, no error, and a container', () => {
    workspace.currentDiff.set(makeDiff());

    component.renderDiff();
    expect(component.createMergeView).not.toHaveBeenCalled();

    component.containerExists = true;
    component.renderDiff();

    expect(shouldRenderMerge(component.diff(), component.containerExists)).toBe(true);
    expect(component.createMergeView).toHaveBeenCalledTimes(1);
  });

  it('should destroy the existing MergeView on destroy', () => {
    const mergeView: DestroyableMergeView = { destroy: jest.fn<void, []>() };
    component.mergeView = mergeView;

    component.ngOnDestroy();

    expect(mergeView.destroy).toHaveBeenCalledTimes(1);
  });
});
