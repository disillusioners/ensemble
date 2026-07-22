import { signal } from '@angular/core';
import type { FileContentResponse } from '../../models/workspace.model';

class MockWorkspaceService {
  readonly currentFile = signal<FileContentResponse | null>(null);
}

class TestableCodeViewerComponent {
  public readonly file;

  constructor(workspace: MockWorkspaceService) {
    this.file = workspace.currentFile.asReadonly();
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }
}

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

describe('CodeViewerComponent logic', () => {
  let workspace: MockWorkspaceService;
  let component: TestableCodeViewerComponent;

  beforeEach(() => {
    workspace = new MockWorkspaceService();
    component = new TestableCodeViewerComponent(workspace);
  });

  it('should format byte sizes below 1024 as integer bytes', () => {
    expect(component.formatSize(512)).toBe('512 B');
  });

  it('should format 1024 bytes as 1.0 KB', () => {
    expect(component.formatSize(1024)).toBe('1.0 KB');
  });

  it('should format 1536 bytes as 1.5 KB', () => {
    expect(component.formatSize(1536)).toBe('1.5 KB');
  });

  it('should format 1048576 bytes as 1.0 MB', () => {
    expect(component.formatSize(1_048_576)).toBe('1.0 MB');
  });

  it('should format 2621440 bytes as 2.5 MB', () => {
    expect(component.formatSize(2_621_440)).toBe('2.5 MB');
  });

  it('should format zero as 0 B', () => {
    expect(component.formatSize(0)).toBe('0 B');
  });

  it('should mirror changes to workspace.currentFile', () => {
    const file = makeFile();

    workspace.currentFile.set(file);

    expect(component.file()).toEqual(file);

    const updated = makeFile({ path: 'src/updated.ts' });
    workspace.currentFile.set(updated);
    expect(component.file()).toEqual(updated);
  });

  it('should default file to null when the workspace signal is null', () => {
    expect(component.file()).toBeNull();
  });
});
