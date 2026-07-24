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
  OpenFileTab,
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

      // selectedPath is now a computed derived from the active tab.
      // Drive it via the public tab API.
      service.openTab('src/main.py');
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

    it('restores project A after loading project B without caching B signals over A', () => {
      const treeA: FileTreeNode[] = [
        { name: 'a', path: 'a', type: 'directory', size: null, children: null },
      ];
      const treeB: FileTreeNode[] = [
        { name: 'b', path: 'b', type: 'directory', size: null, children: null },
      ];

      service.getFileTree('project-a').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-a/tree')
        .flush(makeTreeResponse({ project_id: 'project-a', tree: treeA }));

      service.getFileTree('project-b').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-b/tree')
        .flush(makeTreeResponse({ project_id: 'project-b', tree: treeB }));

      expect(service.restoreState('project-a')?.tree).toEqual(treeA);
      service.connectSSE('project-a');

      expect(service.currentTree()).toEqual(treeA);
      expect(service.restoreState('project-b')?.tree).toEqual(treeB);
    });

    it('does not cache a stale tree under the wrong project when a late HTTP response arrives after a switch (regression)', () => {
      // Simulates the real-world race:
      //   1. Load A — A's tree is in currentTree, ownership = 'project-a'.
      //   2. Start loading B — request is in-flight (slow network).
      //   3. User switches back to A while B's HTTP is still pending.
      //      restoreState('project-a') saves B as null and applies A's
      //      cached tree.
      //   4. B's HTTP response finally arrives. The tap populates
      //      currentTree with B's data even though the active project
      //      is now A. The ownership tag is stamped as 'project-b'.
      //   5. saveCurrentState('project-a') is called. With the old
      //      blind-snapshot bug, B's tree would be cached under A.
      //   6. A subsequent restoreState('project-a') would therefore
      //      return B's tree instead of A's.
      const treeA: FileTreeNode[] = [
        { name: 'a-file', path: 'a-file', type: 'file', size: 1, children: null },
      ];
      const treeB: FileTreeNode[] = [
        { name: 'b-file', path: 'b-file', type: 'file', size: 2, children: null },
      ];

      // 1. Load A.
      service.getFileTree('project-a').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-a/tree')
        .flush(makeTreeResponse({ project_id: 'project-a', tree: treeA }));

      // 2. Start loading B — request stays pending.
      service.getFileTree('project-b').subscribe({ error: () => undefined });

      // 3. Switch back to A from cache while B is still in flight.
      //    This saves B's (currently null) state and applies A's tree.
      service.restoreState('project-a');
      expect(service.currentTree()).toEqual(treeA);

      // 4. B's HTTP response finally arrives — the tap populates
      //    currentTree with B's data while A is the active project.
      //    The ownership tag is stamped as 'project-b' (the project
      //    that initiated the request).
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-b/tree')
        .flush(makeTreeResponse({ project_id: 'project-b', tree: treeB }));

      // 5. The critical saveCurrentState('project-a') call. With the
      //    old blind-snapshot bug, currentTree (= B's tree) would be
      //    cached under A, corrupting A's cache. With the fix, A's
      //    previously cached tree is preserved because the live
      //    signal's ownership tag ('project-b') does not match the
      //    project being saved ('project-a').
      service.saveCurrentState('project-a');

      // 6. Restore A from cache — must still return A's tree, not B's.
      const restoredA = service.restoreState('project-a');
      expect(restoredA!.tree).toEqual(treeA);
      expect(restoredA!.tree).not.toEqual(treeB);
    });

    it('auto-saves the outgoing project state when switching via getFileTree', () => {
      // Load project-1, then switch to project-2. Switching should
      // snapshot project-1's state first.
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse({ tree: [{ name: 'p1', path: '.', type: 'directory', size: null, children: null }] }));

      service.openTab('src/p1.ts');

      // Switch to project-2 — this should auto-save project-1.
      service.getFileTree('project-2').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-2/tree')
        .flush(makeTreeResponse({ project_id: 'project-2' }));

      expect(service.hasCachedState('project-1')).toBe(true);
      const cached = service.restoreState('project-1');
      expect(cached!.selectedPath).toBe('src/p1.ts');
    });

    // Bug 2 — `restoreState(newId)` used to auto-save the outgoing project
    // without any `uiExtras`, so the outgoing snapshot always had empty
    // `expandedPaths` and the default `viewMode`. The component now
    // passes caller-owned UI state through the optional second argument.
    it('threads outgoingUiExtras into the saveCurrentState call for the outgoing project', () => {
      // Setup: project-a is the "current" project. project-b has a
      // pre-existing cache entry. When the component switches A→B it
      // calls `restoreState('project-b', outgoingExtras)`; the extras
      // belong to A (the outgoing project) and must end up in A's cache.
      service.getFileTree('project-a').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-a/tree')
        .flush(makeTreeResponse({ project_id: 'project-a' }));
      service.openTab('src/main.py');
      service.saveCurrentState('project-a', { viewMode: 'code' });

      // Pre-seed project-b's cache WITHOUT touching _currentProjectId.
      service.saveCurrentState('project-b', { viewMode: 'code' });

      // Simulate the FileTreeComponent's expanded set for project-a.
      const outgoingExpandedPaths = ['src', 'src/components'];

      // The component switches A→B, passing A's outgoing uiExtras.
      service.restoreState('project-b', {
        expandedPaths: outgoingExpandedPaths,
        viewMode: 'diff',
      });

      // Project-a's cached state must now include those extras.
      const restoredA = service.restoreState('project-a');
      expect(restoredA!.expandedPaths).toEqual(outgoingExpandedPaths);
      expect(restoredA!.viewMode).toBe('diff');
    });

    it('treats a missing outgoingUiExtras argument as an empty object (back-compat)', () => {
      // Pre-existing callers (and the service's own internal callers)
      // pass only the projectId. The new parameter must default to an
      // empty object so the outgoing snapshot uses the service's own
      // fallback values (default viewMode, empty expandedPaths).
      service.getFileTree('project-a').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-a/tree')
        .flush(makeTreeResponse({ project_id: 'project-a' }));
      service.saveCurrentState('project-a', { viewMode: 'code' });

      // Pre-seed project-b's cache WITHOUT touching _currentProjectId.
      service.saveCurrentState('project-b', { viewMode: 'code' });

      // No second arg — should still work.
      service.restoreState('project-b');

      const restoredA = service.restoreState('project-a');
      // No extras were passed, so the outgoing snapshot falls back to
      // defaults (empty expandedPaths, default viewMode).
      expect(restoredA!.expandedPaths).toEqual([]);
      expect(restoredA!.viewMode).toBe('code');
    });

    // Bug 1 — `currentFile` is intentionally NOT cached. After a project
    // switch, the consumer (component) is expected to refetch the file
    // content. This test pins down the contract: restoreState must
    // always null `currentFile` so the viewer shows a loading state
    // until the refetch lands.
    it('always sets currentFile to null on restore (Bug 1 contract: no file content in cache)', () => {
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse());

      // Open a tab and seed its content via the public tab API.
      // selectedPath is now a computed derived from the active tab,
      // so we drive it through openTab + cacheTabContent.
      service.openTab('src/main.py');
      service.cacheTabContent(makeFileContent());
      service.saveCurrentState('project-1', { viewMode: 'code' });

      // currentFile is populated (derived from the active tab's content).
      expect(service.currentFile()).not.toBeNull();

      // Switching to a cached project must null currentFile regardless
      // of the uiExtras argument — the cache schema does not include it.
      service.saveCurrentState('project-2', { viewMode: 'code' });
      service.restoreState('project-2');

      expect(service.currentFile()).toBeNull();
      // selectedPath IS restored — it's part of the cached state.
      expect(service.selectedPath()).toBeNull();
    });
  });

  // ── Multi-file tab state ──────────────────────────────────────────

  describe('multi-file tabs', () => {
    it('starts with no tabs and null selectedPath/currentFile', () => {
      expect(service.openTabs()).toEqual([]);
      expect(service.activeTabPath()).toBeNull();
      expect(service.selectedPath()).toBeNull();
      expect(service.currentFile()).toBeNull();
      expect(service.hasUnsavedTabs()).toBe(false);
    });

    it('opens a tab and derives selectedPath from the active tab', () => {
      service.openTab('src/main.py');

      expect(service.openTabs()).toEqual([
        { path: 'src/main.py', name: 'main.py', dirty: false },
      ]);
      expect(service.activeTabPath()).toBe('src/main.py');
      expect(service.selectedPath()).toBe('src/main.py');
    });

    it('opens tabs in insertion order (left-to-right) and derives the basename', () => {
      service.openTab('src/a.ts');
      service.openTab('src/components/b.ts');
      service.openTab('c.ts');

      expect(service.openTabs().map((t) => t.path)).toEqual([
        'src/a.ts',
        'src/components/b.ts',
        'c.ts',
      ]);
      expect(service.openTabs().map((t) => t.name)).toEqual([
        'a.ts',
        'b.ts',
        'c.ts',
      ]);
      // Most recently opened becomes the active tab.
      expect(service.activeTabPath()).toBe('c.ts');
    });

    it('reativates an existing tab without duplicating it', () => {
      service.openTab('a.ts');
      service.openTab('b.ts');
      service.openTab('a.ts'); // re-activate

      expect(service.openTabs().map((t) => t.path)).toEqual(['a.ts', 'b.ts']);
      expect(service.activeTabPath()).toBe('a.ts');
    });

    it('setActiveTab is a no-op (returns false) when the path is not open', () => {
      service.openTab('a.ts');
      const result = service.setActiveTab('nope.ts');
      expect(result).toBe(false);
      expect(service.activeTabPath()).toBe('a.ts');
    });

    it('setActiveTab returns true and updates the active tab when the path is open', () => {
      service.openTab('a.ts');
      service.openTab('b.ts');
      const result = service.setActiveTab('a.ts');
      expect(result).toBe(true);
      expect(service.activeTabPath()).toBe('a.ts');
    });

    it('isTabOpen reports open/closed state without side effects', () => {
      expect(service.isTabOpen('a.ts')).toBe(false);
      service.openTab('a.ts');
      expect(service.isTabOpen('a.ts')).toBe(true);
      service.closeTab('a.ts');
      expect(service.isTabOpen('a.ts')).toBe(false);
    });

    it('closes the active tab and activates the right neighbour (preferred)', () => {
      service.openTab('a.ts');
      service.openTab('b.ts');
      service.openTab('c.ts');
      // Active is c.ts (last opened). Close it → b.ts should become active.
      expect(service.closeTab('c.ts')).toBe('b.ts');
      expect(service.openTabs().map((t) => t.path)).toEqual(['a.ts', 'b.ts']);
      expect(service.activeTabPath()).toBe('b.ts');
    });

    it('closes a non-active tab without changing the active tab', () => {
      service.openTab('a.ts');
      service.openTab('b.ts');
      service.openTab('c.ts');
      service.setActiveTab('a.ts');
      // Close b.ts (not active) — active should stay a.ts.
      expect(service.closeTab('b.ts')).toBe('a.ts');
      expect(service.openTabs().map((t) => t.path)).toEqual(['a.ts', 'c.ts']);
      expect(service.activeTabPath()).toBe('a.ts');
    });

    it('closes the last remaining tab and clears activeTabPath', () => {
      service.openTab('a.ts');
      expect(service.closeTab('a.ts')).toBeNull();
      expect(service.openTabs()).toEqual([]);
      expect(service.activeTabPath()).toBeNull();
      expect(service.selectedPath()).toBeNull();
    });

    it('when closing the active tab in a 2-tab list, falls back to the left neighbour', () => {
      service.openTab('a.ts');
      service.openTab('b.ts');
      // Active is b.ts. Close it → only a.ts remains. The right-neighbour
      // lookup at idx=1 clamps to next.length-1=0, so a.ts becomes active.
      expect(service.closeTab('b.ts')).toBe('a.ts');
      expect(service.activeTabPath()).toBe('a.ts');
    });

    it('closing a tab drops its content cache entry', () => {
      service.openTab('a.ts');
      service.cacheTabContent(makeFileContent({ path: 'a.ts', content: 'A' }));
      service.openTab('b.ts');
      service.cacheTabContent(makeFileContent({ path: 'b.ts', content: 'B' }));

      service.setActiveTab('a.ts');
      expect(service.currentFile()?.content).toBe('A');

      service.closeTab('a.ts');
      // a.ts is gone; switching back to b.ts still shows b.ts's content.
      expect(service.currentFile()?.content).toBe('B');

      // Re-opening a.ts starts with no cached content.
      service.openTab('a.ts');
      expect(service.currentFile()).toBeNull();
    });

    it('closeAllTabs clears the tab list, active path, content cache and dirty set', () => {
      service.openTab('a.ts');
      service.cacheTabContent(makeFileContent({ path: 'a.ts' }));
      service.markTabDirty('a.ts');

      service.closeAllTabs();

      expect(service.openTabs()).toEqual([]);
      expect(service.activeTabPath()).toBeNull();
      expect(service.currentFile()).toBeNull();
      expect(service.hasUnsavedTabs()).toBe(false);
    });

    it('markTabDirty / markTabClean update the dirty flag on openTabs', () => {
      service.openTab('a.ts');
      expect(service.openTabs()[0].dirty).toBe(false);

      service.markTabDirty('a.ts');
      expect(service.openTabs()[0].dirty).toBe(true);
      expect(service.hasUnsavedTabs()).toBe(true);

      service.markTabClean('a.ts');
      expect(service.openTabs()[0].dirty).toBe(false);
      expect(service.hasUnsavedTabs()).toBe(false);
    });

    it('markTabDirty is a no-op for a path that is not an open tab', () => {
      service.openTab('a.ts');
      service.markTabDirty('not-open.ts');
      expect(service.hasUnsavedTabs()).toBe(false);
      // The open tab's dirty flag is also untouched.
      expect(service.openTabs()[0].dirty).toBe(false);
    });

    it('markTabClean is a no-op for a path that is not dirty', () => {
      service.openTab('a.ts');
      service.markTabClean('a.ts'); // never dirty
      expect(service.openTabs()[0].dirty).toBe(false);
    });

    it('getFileContent opens a new tab and populates currentFile from the response', (done) => {
      const response = makeFileContent({ path: 'src/main.py', content: 'hello' });

      service.getFileContent('project-1', 'src/main.py').subscribe({
        next: () => {
          expect(service.openTabs().map((t) => t.path)).toEqual(['src/main.py']);
          expect(service.activeTabPath()).toBe('src/main.py');
          expect(service.selectedPath()).toBe('src/main.py');
          expect(service.currentFile()).toEqual(response);
          done();
        },
        error: done.fail,
      });

      httpTesting
        .expectOne(
          (r) => r.url === '/api/workspace/project-1/file' && r.params.get('path') === 'src/main.py'
        )
        .flush(response);
    });

    it('getFileContent on an already-open tab just refreshes the cached content', (done) => {
      service.openTab('src/main.py');
      service.openTab('src/other.py');
      service.setActiveTab('src/main.py');

      const response = makeFileContent({ path: 'src/main.py', content: 'fresh' });
      service.getFileContent('project-1', 'src/main.py').subscribe({
        next: () => {
          // Tab list is unchanged (still two tabs, same order).
          expect(service.openTabs().map((t) => t.path)).toEqual([
            'src/main.py',
            'src/other.py',
          ]);
          expect(service.currentFile()).toEqual(response);
          done();
        },
        error: done.fail,
      });

      httpTesting
        .expectOne(
          (r) => r.url === '/api/workspace/project-1/file' && r.params.get('path') === 'src/main.py'
        )
        .flush(response);
    });

    it('delivers a stale file response without mutating tabs after a generation bump', () => {
      const response = makeFileContent({ path: 'src/main.py', content: 'stale' });
      let delivered: FileContentResponse | undefined;

      service.getFileContent('project-1', 'src/main.py').subscribe((result) => {
        delivered = result;
      });
      const req = httpTesting.expectOne(
        (r) => r.url === '/api/workspace/project-1/file' && r.params.get('path') === 'src/main.py',
      );

      service.resetState();
      req.flush(response);

      expect(delivered).toEqual(response);
      expect(service.openTabs()).toEqual([]);
      expect(service.activeTabPath()).toBeNull();
      expect(service.currentFile()).toBeNull();
    });

    it('re-clicking an already-open tab activates it without refetching content', () => {
      const response = makeFileContent({ path: 'src/main.py', content: 'loaded' });
      service.openFile('project-1', 'src/main.py');
      httpTesting
        .expectOne(
          (r) => r.url === '/api/workspace/project-1/file' && r.params.get('path') === 'src/main.py',
        )
        .flush(response);

      service.openTab('src/other.py');
      expect(service.activeTabPath()).toBe('src/other.py');

      service.openFile('project-1', 'src/main.py');

      expect(service.activeTabPath()).toBe('src/main.py');
      expect(service.currentFile()).toEqual(response);
      httpTesting.expectNone(
        (r) => r.url === '/api/workspace/project-1/file' && r.params.get('path') === 'src/main.py',
      );
    });

    it('ensureTabContent fetches content for a restored tab with an empty cache', () => {
      service.openTab('src/main.py');
      service.saveCurrentState('project-1');
      service.restoreState('project-1');
      expect(service.currentFile()).toBeNull();

      service.ensureTabContent('project-1', 'src/main.py');
      const req = httpTesting.expectOne(
        (r) => r.url === '/api/workspace/project-1/file' && r.params.get('path') === 'src/main.py',
      );
      const response = makeFileContent({ path: 'src/main.py', content: 'restored' });
      req.flush(response);

      expect(service.currentFile()).toEqual(response);
      expect(service.activeTabPath()).toBe('src/main.py');
    });

    it('ensureTabContent skips tabs with cached content and paths that are not open', () => {
      const response = makeFileContent({ path: 'src/main.py', content: 'cached' });
      service.openTab('src/main.py');
      service.cacheTabContent(response);

      service.ensureTabContent('project-1', 'src/main.py');
      service.ensureTabContent('project-1', 'src/not-open.py');

      httpTesting.expectNone((r) => r.url === '/api/workspace/project-1/file');
      expect(service.currentFile()).toEqual(response);
    });

    it('saveFile does NOT mutate currentFile or clean the dirty flag (F2 contract preserved)', (done) => {
      // Open a tab, seed content, mark dirty.
      service.openTab('src/main.py');
      service.cacheTabContent(makeFileContent({ path: 'src/main.py', content: 'old' }));
      service.markTabDirty('src/main.py');

      const saveResponse: FileWriteResponse = {
        project_id: 'project-1',
        path: 'src/main.py',
        size_bytes: 3,
        saved: true,
      };

      service.saveFile('project-1', 'src/main.py', 'new').subscribe({
        next: () => {
          // F2 — the service must NOT mutate the cached content.
          expect(service.currentFile()?.content).toBe('old');
          // F2 — the service does NOT clear dirty here; the component
          // is responsible for calling markTabClean after a successful save.
          expect(service.openTabs()[0].dirty).toBe(true);
          done();
        },
        error: done.fail,
      });

      httpTesting
        .expectOne((r) => r.method === 'PUT' && r.url === '/api/workspace/project-1/file')
        .flush(saveResponse);
    });

    it('resetState clears all tab state', () => {
      service.openTab('a.ts');
      service.cacheTabContent(makeFileContent({ path: 'a.ts' }));
      service.markTabDirty('a.ts');

      service.resetState();

      expect(service.openTabs()).toEqual([]);
      expect(service.activeTabPath()).toBeNull();
      expect(service.selectedPath()).toBeNull();
      expect(service.currentFile()).toBeNull();
      expect(service.hasUnsavedTabs()).toBe(false);
    });

    it('saveCurrentState captures openTabs and activeTabPath', () => {
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse());

      service.openTab('src/a.ts');
      service.openTab('src/b.ts');
      service.setActiveTab('src/a.ts');
      service.saveCurrentState('project-1');

      const cached = service.peekCachedState('project-1');
      expect(cached).not.toBeNull();
      expect(cached!.openTabs.map((t) => t.path)).toEqual(['src/a.ts', 'src/b.ts']);
      expect(cached!.openTabs.map((t) => t.name)).toEqual(['a.ts', 'b.ts']);
      expect(cached!.activeTabPath).toBe('src/a.ts');
      expect(cached!.selectedPath).toBe('src/a.ts');
    });

    it('restoreState restores the tab list and active path but keeps tabs clean', () => {
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse());

      service.openTab('src/a.ts');
      service.openTab('src/b.ts');
      service.setActiveTab('src/a.ts');
      service.markTabDirty('src/a.ts');
      service.saveCurrentState('project-1');

      // Switch to another project and back.
      service.restoreState('project-1');
      expect(service.openTabs().map((t) => t.path)).toEqual(['src/a.ts', 'src/b.ts']);
      expect(service.openTabs().map((t) => t.name)).toEqual(['a.ts', 'b.ts']);
      expect(service.activeTabPath()).toBe('src/a.ts');
      // Dirty is transient and is NOT persisted.
      expect(service.openTabs().every((t) => !t.dirty)).toBe(true);
      // Content is also NOT restored (Bug 1 contract).
      expect(service.currentFile()).toBeNull();
    });

    it('selectedPath and currentFile are readonly signals (cannot be set directly)', () => {
      // The TS compiler rejects `.set()` on computed signals — this is a
      // runtime safeguard that throws via Proxy so a misbehaving caller
      // gets a clear error instead of a silent no-op.
      expect(() => (service.selectedPath as unknown as { set: (v: unknown) => void }).set('x')).toThrow();
      expect(() =>
        (service.currentFile as unknown as { set: (v: unknown) => void }).set(null)
      ).toThrow();
    });

    it('activeTabPath exposes asReadonly so consumers can subscribe without writing', () => {
      // activeTabPath is a readonly view of a writable signal. It exposes
      // a function-call API but not `.set()`, so external callers can't
      // mutate it directly — they must go through openTab/setActiveTab.
      expect(typeof service.activeTabPath).toBe('function');
      service.openTab('a.ts');
      expect(service.activeTabPath()).toBe('a.ts');
      expect(() =>
        (service.activeTabPath as unknown as { set: (v: unknown) => void }).set('b.ts')
      ).toThrow();
    });

    // ── SSE file_changed + dirty-state guard ─────────────────────────
    //
    // The `handleFileChange` method is the SSE-side handler for
    // `file_changed` events. With multi-file tabs it must refetch any
    // matching open tab, but it must also SKIP the refresh when the tab
    // has unsaved edits — otherwise the SSE-driven cache overwrite would
    // clobber the user's in-flight edits (both for the active tab and
    // for an inactive background tab whose cached content would surface
    // over the user's edits on the next activation).

    it('refetches an open tab on SSE file_changed when the tab is clean', () => {
      // Setup: load project so _currentProjectId is set, then open a tab
      // and seed its cached content.
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse());

      service.openTab('src/main.py');
      service.cacheTabContent(
        makeFileContent({ path: 'src/main.py', content: 'old', size_bytes: 3 })
      );
      expect(service.currentFile()?.content).toBe('old');
      expect(service.openTabs()[0].dirty).toBe(false);

      // Simulate an SSE file_changed arriving for this path. The handler
      // is private — invoke it directly via the test escape hatch that
      // the rest of this file already uses for `saveCurrentState` /
      // `restoreState` edge cases.
      (service as unknown as { handleFileChange: (p: string) => void }).handleFileChange(
        'src/main.py',
      );

      // A refetch must have been issued for the matching path.
      const req = httpTesting.expectOne(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/workspace/project-1/file' &&
          r.params.get('path') === 'src/main.py',
      );
      req.flush(
        makeFileContent({ path: 'src/main.py', content: 'fresh', size_bytes: 5 }),
      );

      // The cached content reflects the fresh on-disk version.
      expect(service.currentFile()?.content).toBe('fresh');
      expect(service.currentFile()?.size_bytes).toBe(5);
    });

    it('does NOT refetch an open tab on SSE file_changed when the tab has unsaved edits', () => {
      // Setup: load project, open a tab, seed cached content, mark dirty.
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse());

      service.openTab('src/main.py');
      service.cacheTabContent(
        makeFileContent({ path: 'src/main.py', content: 'old', size_bytes: 3 })
      );
      service.markTabDirty('src/main.py');
      expect(service.openTabs()[0].dirty).toBe(true);

      // SSE file_changed for the dirty path.
      (service as unknown as { handleFileChange: (p: string) => void }).handleFileChange(
        'src/main.py',
      );

      // NO HTTP call should be made — the dirty tab's edits must not be
      // overwritten by an SSE-driven refetch. httpTesting.expectNone
      // fails if any matching request was issued; httpTesting.verify()
      // in afterEach is the belt-and-suspenders backstop.
      httpTesting.expectNone(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/workspace/project-1/file' &&
          r.params.get('path') === 'src/main.py',
      );

      // Cached content is preserved verbatim.
      expect(service.currentFile()?.content).toBe('old');
      expect(service.currentFile()?.size_bytes).toBe(3);
      // Tab is still dirty.
      expect(service.openTabs()[0].dirty).toBe(true);
    });

    it('does NOT refetch a non-active dirty tab on SSE file_changed', () => {
      // Setup: load project, open two tabs, switch focus, mark the
      // INACTIVE tab dirty.
      service.getFileTree('project-1').subscribe({ error: () => undefined });
      httpTesting
        .expectOne((r) => r.url === '/api/workspace/project-1/tree')
        .flush(makeTreeResponse());

      service.openTab('src/main.py');
      service.openTab('src/other.py');
      service.setActiveTab('src/main.py'); // src/other.py is now inactive
      service.cacheTabContent(
        makeFileContent({ path: 'src/other.py', content: 'unsaved edits' }),
      );
      service.markTabDirty('src/other.py');

      // SSE file_changed for the inactive dirty tab.
      (service as unknown as { handleFileChange: (p: string) => void }).handleFileChange(
        'src/other.py',
      );

      // No refetch for the dirty inactive path.
      httpTesting.expectNone(
        (r) =>
          r.method === 'GET' &&
          r.url === '/api/workspace/project-1/file' &&
          r.params.get('path') === 'src/other.py',
      );

      // The dirty tab's cached content and dirty flag are preserved. This
      // matters because the user might switch to this tab shortly — the
      // service must serve the in-memory edits, not a stale on-disk
      // version, on that activation.
      const other = service.openTabs().find((t) => t.path === 'src/other.py');
      expect(other).toBeDefined();
      expect(other!.dirty).toBe(true);
      expect(other!.name).toBe('other.py');
    });
  });
});
