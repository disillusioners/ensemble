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
  FileWriteResponse,
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

  it('should save the selected file via PUT and return the response (F2 — service is side-effect free)', (done) => {
    // Load a file first so currentFile is populated.
    const loaded = makeFileContent({
      path: 'src/main.py',
      content: 'old content',
      size_bytes: 11,
    });
    service.getFileContent('project-1', 'src/main.py').subscribe({ error: () => undefined });
    httpTesting
      .expectOne((r) => r.url === '/api/workspace/project-1/file')
      .flush(loaded);

    const newContent = 'new content';
    const saveResponse: FileWriteResponse = {
      project_id: 'project-1',
      path: 'src/main.py',
      size_bytes: newContent.length,
      saved: true,
    };

    service.saveFile('project-1', 'src/main.py', newContent).subscribe({
      next: (result) => {
        expect(result).toEqual(saveResponse);
        // F2 — the service MUST NOT mutate `currentFile` after a save.
        // The previous behaviour (tap that broadcasted the saved content
        // back through `currentFile`) caused the CodeViewerComponent's
        // effect to reset `editedContent`, clobbering keystrokes typed
        // while the PUT was in flight. Dirty-state management is now
        // owned by the component (`codeViewer.markSaved()`).
        expect(service.currentFile()?.content).toBe('old content');
        expect(service.currentFile()?.size_bytes).toBe(11);
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'PUT' &&
        request.url === '/api/workspace/project-1/file'
    );
    expect(req.request.body).toEqual({ path: 'src/main.py', content: newContent });
    req.flush(saveResponse);
  });

  it('should never mutate currentFile when saving a different path (F2 — service is side-effect free)', (done) => {
    const loaded = makeFileContent({
      path: 'src/main.py',
      content: 'old content',
      size_bytes: 11,
    });
    service.getFileContent('project-1', 'src/main.py').subscribe({ error: () => undefined });
    httpTesting
      .expectOne((r) => r.url === '/api/workspace/project-1/file')
      .flush(loaded);

    const saveResponse: FileWriteResponse = {
      project_id: 'project-1',
      path: 'src/other.py',
      size_bytes: 5,
      saved: true,
    };

    service.saveFile('project-1', 'src/other.py', 'hello').subscribe({
      next: () => {
        // Saving an unrelated file must not touch currentFile either —
        // the service no longer mutates currentFile under any save path.
        expect(service.currentFile()?.content).toBe('old content');
        expect(service.currentFile()?.path).toBe('src/main.py');
        expect(service.currentFile()?.size_bytes).toBe(11);
        done();
      },
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'PUT' &&
        request.url === '/api/workspace/project-1/file'
    );
    expect(req.request.body).toEqual({ path: 'src/other.py', content: 'hello' });
    req.flush(saveResponse);
  });

  it('should re-throw a save 500 and MUST NOT set the error signal (F8 — single error presentation)', (done) => {
    // F8 — save errors are surfaced by the consumer's snackbar, not by
    // the service's error banner. The catchError rethrows without
    // touching `this.error` to avoid the double-banner UX.
    service.error.set('Previous failure'); // Seed to prove we don't overwrite.

    service.saveFile('project-1', 'src/main.py', 'hello').subscribe({
      next: () => done.fail('expected error'),
      error: (error) => {
        expect(error.status).toBe(500);
        // The error signal is NOT mutated by saveFile catchError.
        expect(service.error()).toBe('Previous failure');
        done();
      },
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'PUT' &&
        request.url === '/api/workspace/project-1/file'
    );
    req.flush('Server error', { status: 500, statusText: 'Server Error' });
  });

  it('should URL-encode the project id on saveFile', (done) => {
    service.saveFile('team/project with spaces', 'src/main.py', 'x').subscribe({
      next: () => done(),
      error: done.fail,
    });

    const req = httpTesting.expectOne(
      (request) =>
        request.method === 'PUT' &&
        request.url === '/api/workspace/team%2Fproject%20with%20spaces/file'
    );
    req.flush({
      project_id: 'team/project with spaces',
      path: 'src/main.py',
      size_bytes: 1,
      saved: true,
    });
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

  // ── LRU per-project state cache ───────────────────────────────────

  describe('LRU per-project state cache', () => {
    it('exposes a maxCachedProjects of 5', () => {
      expect(service.maxCachedProjects).toBe(5);
    });

    it('returns null from restoreState when nothing is cached', () => {
      expect(service.restoreState('nope')).toBeNull();
      expect(service.hasCachedState('nope')).toBe(false);
    });

    it('snapshots the current signals and returns them from restoreState', () => {
      const response = makeTreeResponse();
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      const req = httpTesting.expectOne(
        (r) => r.url === '/api/workspace/project-1/tree'
      );
      req.flush(response);

      service.selectedPath.set('src/main.py');
      service.saveCurrentState('project-1', { viewMode: 'diff' });

      expect(service.hasCachedState('project-1')).toBe(true);

      const restored = service.restoreState('project-1');
      expect(restored).not.toBeNull();
      expect(restored!.projectId).toBe('project-1');
      expect(restored!.tree).toEqual(response.tree);
      expect(restored!.selectedPath).toBe('src/main.py');
      expect(restored!.viewMode).toBe('diff');

      // Signals are also re-applied from the cache.
      expect(service.currentTree()).toEqual(response.tree);
      expect(service.selectedPath()).toBe('src/main.py');
    });

    it('preserves prior cached extras when a partial save is provided', () => {
      const response = makeTreeResponse();
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting.expectOne((r) => r.url === '/api/workspace/project-1/tree').flush(response);

      // First save includes both viewMode and expandedPaths.
      service.saveCurrentState('project-1', {
        viewMode: 'diff',
        expandedPaths: ['src', 'src/components'],
      });
      // Second save only updates viewMode — expandedPaths must survive.
      service.saveCurrentState('project-1', { viewMode: 'code' });

      const restored = service.restoreState('project-1');
      expect(restored!.viewMode).toBe('code');
      expect(restored!.expandedPaths).toEqual(['src', 'src/components']);
    });

    it('ignores saveCurrentState when projectId is empty', () => {
      service.saveCurrentState('');
      expect(service.cacheSize()).toBe(0);
      expect(service.hasCachedState('')).toBe(false);
    });

    it('evicts the least-recently-used entry when exceeding capacity', () => {
      const tree = makeTreeResponse().tree;
      // Fill the cache with 5 distinct projects.
      for (let i = 1; i <= 5; i++) {
        const pid = `project-${i}`;
        service.saveCurrentState(pid, { viewMode: 'code' });
        // Force currentTree() to vary so saves capture meaningful state.
        service.currentTree.set([{ ...tree[0], name: pid }]);
      }
      expect(service.cacheSize()).toBe(5);

      // Touch project-1 to make it most-recently-used, then add a 6th.
      service.saveCurrentState('project-1');
      service.saveCurrentState('project-6', { viewMode: 'code' });

      expect(service.cacheSize()).toBe(5);
      expect(service.hasCachedState('project-1')).toBe(true);
      // project-2 should have been evicted (oldest untouched entry).
      expect(service.hasCachedState('project-2')).toBe(false);
      expect(service.hasCachedState('project-6')).toBe(true);
    });

    it('promotes an existing entry to most-recently-used on save', () => {
      service.saveCurrentState('project-a');
      service.saveCurrentState('project-b');
      service.saveCurrentState('project-c');

      // Re-save project-a — it should now be at the tail (MRU) and
      // project-b the head (LRU).
      service.saveCurrentState('project-a');
      // Adding 4 more projects will evict b, c, d, e in that order, but
      // project-a should always survive.
      for (const pid of ['d', 'e', 'f']) {
        service.saveCurrentState(`project-${pid}`);
      }
      // project-a should still be present.
      expect(service.hasCachedState('project-a')).toBe(true);
    });

    it('clears a single entry via clearCache(projectId)', () => {
      service.saveCurrentState('project-x');
      service.saveCurrentState('project-y');
      expect(service.cacheSize()).toBe(2);

      service.clearCache('project-x');

      expect(service.hasCachedState('project-x')).toBe(false);
      expect(service.hasCachedState('project-y')).toBe(true);
      expect(service.cacheSize()).toBe(1);
    });

    it('clears the entire cache when clearCache is called with no arg', () => {
      service.saveCurrentState('project-x');
      service.saveCurrentState('project-y');

      service.clearCache();

      expect(service.cacheSize()).toBe(0);
      expect(service.hasCachedState('project-x')).toBe(false);
      expect(service.hasCachedState('project-y')).toBe(false);
    });

    it('marks an entry as most-recently-used when restored', () => {
      service.saveCurrentState('project-a');
      service.saveCurrentState('project-b');
      service.saveCurrentState('project-c');
      service.saveCurrentState('project-d');
      service.saveCurrentState('project-e');
      expect(service.cacheSize()).toBe(5);

      // Touch 'a' via restore so it becomes MRU.
      service.restoreState('project-a');

      // Add 2 more projects — a should survive; b should be evicted
      // first since it's the LRU after the touch.
      service.saveCurrentState('project-f');
      service.saveCurrentState('project-g');

      expect(service.hasCachedState('project-a')).toBe(true);
      expect(service.hasCachedState('project-b')).toBe(false);
      expect(service.hasCachedState('project-g')).toBe(true);
    });

    it('auto-saves the outgoing project state when switching via getFileTree', () => {
      // Load project-1, then switch to project-2. Switching should
      // snapshot project-1's state first.
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse({ tree: [{ name: 'p1', path: '.', type: 'directory', size: null, children: null }] }));

      service.selectedPath.set('src/p1.ts');

      // Switch to project-2 — this should auto-save project-1.
      service.getFileTree('project-2').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-2/tree')
        .flush(makeTreeResponse({ project_id: 'project-2' }));

      expect(service.hasCachedState('project-1')).toBe(true);
      const cached = service.restoreState('project-1');
      expect(cached!.selectedPath).toBe('src/p1.ts');
    });
  });
});
