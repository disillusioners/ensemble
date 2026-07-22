import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { WorkspaceService } from './workspace.service';
import {
  FileContentResponse,
  FileTreeNode,
  FileTreeResponse,
  GitDiffResponse,
} from '../models/workspace.model';

function makeTreeResponse(overrides: Partial<FileTreeResponse> = {}): FileTreeResponse {
  return {
    project_id: 'project-1',
    path: '.',
    tree: [
      {
        name: 'src',
        path: 'src',
        type: 'directory',
        size: null,
        children: null,
      },
    ],
    truncated: false,
    ...overrides,
  };
}

function makeFileContent(
  overrides: Partial<FileContentResponse> = {}
): FileContentResponse {
  return {
    project_id: 'project-1',
    path: 'src/main.py',
    content: 'print("hello")',
    language: 'python',
    total_lines: 1,
    offset: 0,
    limit: 1000,
    truncated: false,
    binary: false,
    size_bytes: 14,
    ...overrides,
  };
}

function makeDiff(overrides: Partial<GitDiffResponse> = {}): GitDiffResponse {
  return {
    project_id: 'project-1',
    path: 'src/main.py',
    has_changes: true,
    diff: '-old\n+new',
    head_content: 'old',
    working_content: 'new',
    error: null,
    ...overrides,
  };
}

describe('WorkspaceService', () => {
  let service: WorkspaceService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(WorkspaceService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTesting.verify();
  });

  it("should get the file tree with the default path '.'", (done) => {
    const response = makeTreeResponse();

    service.getFileTree('project-1').subscribe({
      next: (result) => {
        expect(result).toEqual(response);
        expect(service.currentTree()).toEqual(response.tree);
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'GET' &&
        request.url === '/api/workspace/project-1/tree' &&
        request.params.get('path') === '.'
    );
    req.flush(response);
  });

  it('should get the file tree with a custom path', (done) => {
    const response = makeTreeResponse({ path: 'src/components', tree: [] });

    service.getFileTree('project-1', 'src/components').subscribe({
      next: (result) => {
        expect(result).toEqual(response);
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.url === '/api/workspace/project-1/tree' &&
        request.params.get('path') === 'src/components'
    );
    req.flush(response);
  });

  it('should swallow a tree 404, set error, and return an empty response', (done) => {
    service.getFileTree('project-1', 'missing').subscribe({
      next: (result) => {
        expect(result).toEqual({
          project_id: 'project-1',
          path: 'missing',
          tree: [],
          truncated: false,
        });
        expect(service.error()).toContain('404');
        expect(service.currentTree()).toBeNull();
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.url === '/api/workspace/project-1/tree' &&
        request.params.get('path') === 'missing'
    );
    req.flush('Not found', { status: 404, statusText: 'Not Found' });
  });

  it('should update currentFile and selectedPath after loading content', (done) => {
    const response = makeFileContent();

    service.getFileContent('project-1', 'src/main.py').subscribe({
      next: (result) => {
        expect(result).toEqual(response);
        expect(service.currentFile()).toEqual(response);
        expect(service.selectedPath()).toBe('src/main.py');
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'GET' &&
        request.url === '/api/workspace/project-1/file' &&
        request.params.get('path') === 'src/main.py'
    );
    req.flush(response);
  });

  it('should re-throw a file-content 500', (done) => {
    service.getFileContent('project-1', 'src/main.py').subscribe({
      next: () => done.fail('expected error'),
      error: (error) => {
        expect(error.status).toBe(500);
        expect(service.error()).toContain('500');
        done();
      },
    });

    const req = httpTesting.expectOne('/api/workspace/project-1/file?path=src/main.py');
    req.flush('Server error', { status: 500, statusText: 'Server Error' });
  });

  it('should update currentDiff after loading a diff', (done) => {
    const response = makeDiff();

    service.getFileDiff('project-1', 'src/main.py').subscribe({
      next: (result) => {
        expect(result).toEqual(response);
        expect(service.currentDiff()).toEqual(response);
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'GET' &&
        request.url === '/api/workspace/project-1/diff' &&
        request.params.get('path') === 'src/main.py'
    );
    req.flush(response);
  });

  it('should re-throw a diff 500', (done) => {
    service.getFileDiff('project-1', 'src/main.py').subscribe({
      next: () => done.fail('expected error'),
      error: (error) => {
        expect(error.status).toBe(500);
        expect(service.error()).toContain('500');
        done();
      },
    });

    const req = httpTesting.expectOne('/api/workspace/project-1/diff?path=src/main.py');
    req.flush('Server error', { status: 500, statusText: 'Server Error' });
  });

  it('should expand a directory by requesting its node path', (done) => {
    const node: FileTreeNode = {
      name: 'components',
      path: 'src/components',
      type: 'directory',
      size: null,
      children: null,
    };

    service.expandDirectory('project-1', node).subscribe({
      next: () => done(),
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.url === '/api/workspace/project-1/tree' &&
        request.params.get('path') === node.path
    );
    req.flush(makeTreeResponse({ path: node.path, tree: [] }));
  });

  it('should URL-encode the project id', (done) => {
    service.getFileTree('team/project with spaces').subscribe({
      next: () => done(),
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.url === '/api/workspace/team%2Fproject%20with%20spaces/tree' &&
        request.params.get('path') === '.'
    );
    req.flush(makeTreeResponse({ project_id: 'team/project with spaces' }));
  });

  it('should clear the error signal', () => {
    service.error.set('Previous failure');

    service.clearError();

    expect(service.error()).toBeNull();
  });
});
