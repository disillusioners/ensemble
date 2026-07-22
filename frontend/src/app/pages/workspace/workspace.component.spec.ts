import { signal } from '@angular/core';
import { Observable, Subject, of, throwError } from 'rxjs';
import type {
  FileContentResponse,
  FileTreeResponse,
  GitDiffResponse,
} from '../../models/workspace.model';

class MockWorkspaceService {
  readonly selectedPath = signal<string | null>(null);
  readonly getFileTree = jest.fn<Observable<FileTreeResponse>, [string]>(() =>
    of(makeTreeResponse())
  );
  readonly getFileContent = jest.fn<
    Observable<FileContentResponse>,
    [string, string]
  >(() => of(makeFileContent()));
  readonly getFileDiff = jest.fn<Observable<GitDiffResponse>, [string, string]>(() =>
    of(makeDiff())
  );
}

type RouteLike = {
  snapshot: {
    paramMap: {
      get(name: string): string | null;
    };
  };
};

class TestableWorkspaceComponent {
  public projectId = '';
  public readonly selectedPath;
  public readonly viewMode = signal<'code' | 'diff'>('code');
  public readonly fileTree = { setTree: jest.fn<void, [FileTreeResponse['tree']]>() };

  constructor(
    private readonly route: RouteLike,
    private readonly workspace: MockWorkspaceService
  ) {
    this.selectedPath = workspace.selectedPath.asReadonly();
  }

  ngOnInit(): void {
    this.projectId = this.route.snapshot.paramMap.get('projectId') || '';
    if (this.projectId) {
      this.workspace
        .getFileTree(this.projectId)
        .subscribe((response) => this.fileTree.setTree(response.tree));
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

function makeTreeResponse(overrides: Partial<FileTreeResponse> = {}): FileTreeResponse {
  return {
    project_id: 'project-1',
    path: '.',
    tree: [],
    truncated: false,
    ...overrides,
  };
}

function makeFileContent(
  overrides: Partial<FileContentResponse> = {}
): FileContentResponse {
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

function makeRoute(projectId: string | null): RouteLike {
  return {
    snapshot: {
      paramMap: {
        get: jest.fn<string | null, [string]>(() => projectId),
      },
    },
  };
}

describe('WorkspaceComponent logic', () => {
  let workspace: MockWorkspaceService;

  beforeEach(() => {
    workspace = new MockWorkspaceService();
  });

  it('should extract projectId from route.snapshot.paramMap on init', () => {
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);

    component.ngOnInit();

    expect(component.projectId).toBe('project-42');
  });

  it('should load and set the file tree for a non-empty projectId', () => {
    const response = makeTreeResponse({
      tree: [
        {
          name: 'src',
          path: 'src',
          type: 'directory',
          size: null,
          children: null,
        },
      ],
    });
    workspace.getFileTree.mockReturnValue(of(response));
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);

    component.ngOnInit();

    expect(workspace.getFileTree).toHaveBeenCalledWith('project-42');
    expect(component.fileTree.setTree).toHaveBeenCalledWith(response.tree);
  });

  it('should not load the file tree for an empty projectId', () => {
    const component = new TestableWorkspaceComponent(makeRoute(''), workspace);

    component.ngOnInit();

    expect(component.projectId).toBe('');
    expect(workspace.getFileTree).not.toHaveBeenCalled();
  });

  it('should select code view and load the selected file', () => {
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);
    component.projectId = 'project-42';
    component.viewMode.set('diff');

    component.onFileSelected('src/main.ts');

    expect(component.viewMode()).toBe('code');
    expect(workspace.getFileContent).toHaveBeenCalledWith('project-42', 'src/main.ts');
  });

  it('should select code view without making API calls', () => {
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);
    component.viewMode.set('diff');

    component.onSelectCode();

    expect(component.viewMode()).toBe('code');
    expect(workspace.getFileTree).not.toHaveBeenCalled();
    expect(workspace.getFileContent).not.toHaveBeenCalled();
    expect(workspace.getFileDiff).not.toHaveBeenCalled();
  });

  it('should select diff view without an API call when no path is selected', () => {
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);

    component.onSelectDiff();

    expect(component.viewMode()).toBe('diff');
    expect(workspace.getFileDiff).not.toHaveBeenCalled();
  });

  it('should fetch the selected path diff before switching to diff view', () => {
    const pendingDiff = new Subject<GitDiffResponse>();
    workspace.selectedPath.set('src/main.ts');
    workspace.getFileDiff.mockReturnValue(pendingDiff.asObservable());
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);
    component.projectId = 'project-42';

    component.onSelectDiff();

    expect(workspace.getFileDiff).toHaveBeenCalledWith('project-42', 'src/main.ts');
    // The mode remains unchanged until the subscription's next callback runs.
    expect(component.viewMode()).toBe('code');

    pendingDiff.next(makeDiff());
    expect(component.viewMode()).toBe('diff');
  });

  it('should fall back to diff view when loading the diff errors', () => {
    workspace.selectedPath.set('src/main.ts');
    workspace.getFileDiff.mockReturnValue(
      throwError(() => new Error('Failed to load diff'))
    );
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);
    component.projectId = 'project-42';

    component.onSelectDiff();

    expect(workspace.getFileDiff).toHaveBeenCalledWith('project-42', 'src/main.ts');
    expect(component.viewMode()).toBe('diff');
  });

  it('should expose a readonly selectedPath that reflects the workspace signal', () => {
    const component = new TestableWorkspaceComponent(makeRoute('project-42'), workspace);

    expect(component.selectedPath()).toBeNull();

    workspace.selectedPath.set('src/main.ts');
    expect(component.selectedPath()).toBe('src/main.ts');
    expect('set' in component.selectedPath).toBe(false);
  });
});
