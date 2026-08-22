import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { ActivatedRoute } from '@angular/router';
import { By } from '@angular/platform-browser';
import { Subject, of } from 'rxjs';
import { Component } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatDialog } from '@angular/material/dialog';
import { WorkspaceComponent } from './workspace.component';
import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeComponent } from '../../components/file-tree/file-tree.component';
import { CodeViewerComponent } from '../../components/code-viewer/code-viewer.component';
import { ConfirmDialogComponent } from '../../components/confirm-dialog/confirm-dialog.component';
import type {
  FileContentResponse,
  FileTreeResponse,
  FileWriteResponse,
  GitDiffResponse,
} from '../../models/workspace.model';

/**
 * Lightweight host component used to drive the workspace through a
 * template-bound `[projectId]` input. Lets us exercise the overlay use
 * case (host-driven input, route absent) reliably from a test.
 */
@Component({
  standalone: true,
  imports: [WorkspaceComponent],
  template: `<app-workspace
    [projectId]="projectId"
    (hide)="hideCount = (hideCount ?? 0) + 1"
  ></app-workspace>`,
})
class WorkspaceHostComponent {
  projectId = '';
  hideCount = 0;
}

/**
 * The dialog spy is installed on the same MatDialog instance injected into
 * WorkspaceComponent. This avoids depending on MatDialogModule's provider
 * precedence while keeping `afterClosed()` synchronous and controllable.
 */
let nextDialogResult: boolean | undefined;
let dialogOpenSpy: jest.SpyInstance;

/**
 * Tests for `WorkspaceComponent`.
 *
 * Pattern: Angular `TestBed` with the REAL `WorkspaceComponent` plus a
 * stubbed `ActivatedRoute` and `HttpTestingController`. Driving the
 * component through TestBed lets us verify the integrated behaviours:
 *
 *   - ngOnInit reads `projectId` from the route and triggers the
 *     `getFileTree` HTTP request against the real service.
 *   - `onFileSelected` switches to code view and fires
 *     `getFileContent`.
 *   - `onSelectDiff` with a selected path triggers `getFileDiff` and
 *     ONLY switches the view mode to `diff` once the HTTP response
 *     lands. This is the critical diff-before-switch invariant.
 *   - `onSelectDiff` falls back to `diff` view even when the HTTP
 *     request errors.
 */
describe('WorkspaceComponent', () => {
  class StubEventSource {
    static instances: StubEventSource[] = [];
    url: string;
    onerror: ((e: Event) => void) | null = null;
    close = jest.fn();
    addEventListener = jest.fn();

    constructor(url: string) {
      this.url = url;
      StubEventSource.instances.push(this);
    }
  }

  (globalThis as any).EventSource = StubEventSource;

  let fixture: ComponentFixture<WorkspaceComponent>;
  let component: WorkspaceComponent;
  let httpMock: HttpTestingController;
  let workspaceService: WorkspaceService;

  // ── Factory helpers ────────────────────────────────────────────

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

  function makeWriteResponse(
    overrides: Partial<FileWriteResponse> = {}
  ): FileWriteResponse {
    return {
      project_id: 'test-project-id',
      path: 'src/main.ts',
      size_bytes: 16,
      saved: true,
      ...overrides,
    };
  }

  /**
   * Drive the workspace to a "file selected and loaded" state. Flushes
   * the initial tree request and the file content request so subsequent
   * tests can mutate the dirty flag or assert save behaviour.
   */
  function selectFile(path: string = 'src/main.ts'): FileContentResponse {
    component.onFileSelected(path);
    const req = httpMock.expectOne(
      (r) =>
        r.url === '/api/workspace/test-project-id/file' &&
        r.params.get('path') === path
    );
    expect(req.request.method).toBe('GET');
    const response = makeFileContent({ path });
    req.flush(response);
    fixture.detectChanges();
    return response;
  }

  /**
   * Drive `selectedPath` (and the active file) to a specific value
   * without firing an HTTP request. `selectedPath` is now a computed
   * signal derived from the service's private `_activeFilePath`, so
   * the only way to set it from outside is to reach the underlying
   * writable signal. We use a type cast rather than expose the signal
   * publicly just for tests.
   */
  function setSelectedPath(path: string | null): void {
    (workspaceService as unknown as { _activeFilePath: { set: (v: string | null) => void } })
      ._activeFilePath.set(path);
  }

  /**
   * Drive `currentFile` to a specific `FileContentResponse` without
   * firing an HTTP request. `currentFile` is a computed over
   * `_activeFilePath` + the per-path content cache; updating both
   * makes the computed reflect the new value on the next read.
   */
  function setCurrentFile(file: FileContentResponse): void {
    workspaceService.cacheTabContent(file);
    setSelectedPath(file.path);
    fixture.detectChanges();
  }

  /**
   * Locate the embedded CodeViewerComponent and mark it dirty by
   * diverging `editedContent` from `originalContent`. Returns the
   * viewer's component instance so callers can assert other state.
   */
  function markDirty(): CodeViewerComponent {
    const codeViewerDebug = fixture.debugElement.query(
      By.directive(CodeViewerComponent)
    );
    const codeViewer = codeViewerDebug.componentInstance as CodeViewerComponent;
    codeViewer.editedContent.set('export const modified = true;');
    fixture.detectChanges();
    return codeViewer;
  }

  /**
   * Flush the initial `getFileTree` request that ngOnInit fires.
   * Tests that need a non-empty tree can override the response before
   * calling this helper.
   *
   * Also drains the `loadValidatedWorkdir` request that `loadProject`
   * fires alongside the tree fetch — both must be drained before
   * `httpMock.verify()` runs.
   */
  function flushInitialTree(): void {
    const req = httpMock.expectOne(
      (r) => r.url === '/api/workspace/test-project-id/tree' && r.params.get('path') === '.'
    );
    req.flush(makeTreeResponse());
    flushValidatedWorkdir('test-project-id');
  }

  /**
   * Drain the `GET /api/settings/editor` request that the
   * WorkspaceService constructor fires when the service is
   * instantiated. Without this the `httpMock.verify()` call in the
   * outer `afterEach` fails because the editor-preference request is
   * left open. Default response is `{ editor: 'builtin' }` so the
   * signal stays at its default; tests that need a different value can
   * flush the request manually before calling this helper.
   */
  function flushEditorPreference(): void {
    const req = httpMock.expectOne('/api/settings/editor');
    req.flush({ editor: 'builtin' });
  }

  /**
   * Drain the C2 `GET /api/projects/{id}/vscode-folder` request that
   * `loadValidatedWorkdir` fires when `loadProject` is called. The
   * default response is an empty folder so the iframe URL stays at
   * `/vscode/`; tests that need a specific folder can flush the
   * request manually before calling this helper.
   */
  function flushValidatedWorkdir(projectId: string = 'test-project-id'): void {
    const req = httpMock.expectOne(`/api/projects/${projectId}/vscode-folder`);
    req.flush({ folder: '' });
  }

  // ── TestBed setup ──────────────────────────────────────────────

  beforeEach(async () => {
    StubEventSource.instances = [];
    nextDialogResult = undefined;
    await TestBed.configureTestingModule({
      imports: [WorkspaceComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        WorkspaceService,
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { paramMap: { get: () => 'test-project-id' } },
          },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(WorkspaceComponent);
    dialogOpenSpy = jest
      .spyOn(fixture.debugElement.injector.get(MatDialog), 'open')
      .mockReturnValue({
        afterClosed: () => of(nextDialogResult),
      } as any);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    workspaceService = TestBed.inject(WorkspaceService);
    // Drain the editor-preference request that the WorkspaceService
    // constructor fires the moment it is injected. Without this the
    // outer `afterEach(() => httpMock.verify())` fails because the
    // request is left open.
    flushEditorPreference();
  });

  afterEach(() => {
    httpMock.verify();
  });

  // ── 1) Component creation ─────────────────────────────────────

  it('creates successfully', () => {
    expect(component).toBeTruthy();
  });

  it('renders app-file-tree when editorMode is builtin', () => {
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('app-file-tree')).toBeTruthy();
    flushInitialTree();
  });

  it('removes app-file-tree from DOM when editorMode is vscode', () => {
    workspaceService.setEditorMode('vscode');
    fixture.detectChanges();
    flushInitialTree();

    expect(fixture.nativeElement.querySelector('app-file-tree')).toBeNull();
  });

  it('preserves expanded paths across mode switch', async () => {
    fixture.detectChanges();
    flushInitialTree();

    const getExpandedPathsSpy = jest
      .spyOn(FileTreeComponent.prototype, 'getExpandedPaths')
      .mockReturnValue(['src', 'src/app']);
    const restoreExpandedPathsSpy = jest.spyOn(
      FileTreeComponent.prototype,
      'restoreExpandedPaths',
    );
    const setTreeSpy = jest.spyOn(FileTreeComponent.prototype, 'setTree');

    workspaceService.setEditorMode('vscode');
    fixture.detectChanges();
    TestBed.flushEffects();
    workspaceService.setEditorMode('builtin');
    fixture.detectChanges();
    TestBed.flushEffects();
    await Promise.resolve();

    expect(getExpandedPathsSpy).toHaveBeenCalledWith();
    expect(restoreExpandedPathsSpy).toHaveBeenCalledWith(['src', 'src/app']);
    expect(setTreeSpy).toHaveBeenCalled();
    getExpandedPathsSpy.mockRestore();
    restoreExpandedPathsSpy.mockRestore();
    setTreeSpy.mockRestore();
  });

  it('does not throw when accessing fileTree in vscode mode', () => {
    fixture.detectChanges();
    flushInitialTree();
    workspaceService.setEditorMode('vscode');
    fixture.detectChanges();

    expect(() => component.ngOnInit()).not.toThrow();
    flushInitialTree();
  });


  describe('ngOnInit', () => {
    it('should extract projectId from the route snapshot', () => {
      fixture.detectChanges();
      expect(component.projectId).toBe('test-project-id');
      flushInitialTree();
    });

    it('should load the file tree for the project', () => {
      fixture.detectChanges();
      const req = httpMock.expectOne(
        (r) =>
          r.url === '/api/workspace/test-project-id/tree' &&
          r.params.get('path') === '.'
      );
      expect(req.request.method).toBe('GET');
      req.flush(makeTreeResponse());
      flushValidatedWorkdir('test-project-id');
    });

    it('should populate the embedded FileTreeComponent via setTree', () => {
      fixture.detectChanges();
      const req = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      req.flush(
        makeTreeResponse({
          tree: [
            {
              name: 'src',
              path: 'src',
              type: 'directory',
              size: null,
              children: null,
            },
          ],
        })
      );
      // Drain the validated-workdir request that fires on the
      // cache-miss path so the iframe can open the right folder.
      flushValidatedWorkdir('test-project-id');

      // Reach the embedded FileTreeComponent via the DOM to verify the
      // component really did setTree() — its `fileTree` viewchild is
      // private, so we can't access it through componentInstance.
      const fileTreeDebug = fixture.debugElement.query(
        By.directive(FileTreeComponent)
      );
      const fileTree = fileTreeDebug.componentInstance as FileTreeComponent;
      expect(fileTree.dataSource.data.length).toBe(1);
      expect(fileTree.dataSource.data[0].name).toBe('src');
    });
  });

  describe('ngOnInit with empty projectId', () => {
    beforeEach(async () => {
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [WorkspaceComponent],
        providers: [
          provideHttpClient(),
          provideHttpClientTesting(),
          provideNoopAnimations(),
          WorkspaceService,
          {
            provide: ActivatedRoute,
            useValue: {
              snapshot: { paramMap: { get: () => '' } },
            },
          },
        ],
      }).compileComponents();
      fixture = TestBed.createComponent(WorkspaceComponent);
      component = fixture.componentInstance;
      httpMock = TestBed.inject(HttpTestingController);
      workspaceService = TestBed.inject(WorkspaceService);
      // Drain the editor-preference request that fires when the
      // service is constructed.
      flushEditorPreference();
    });

    it('should set projectId to empty and skip the tree load', () => {
      fixture.detectChanges();
      expect(component.projectId).toBe('');
      // No HTTP request should have been issued.
      httpMock.expectNone(() => true);
    });
  });

  // ── 3) onFileSelected switches to code and loads file ─────────

  describe('onFileSelected', () => {
    beforeEach(() => {
      fixture.detectChanges();
      flushInitialTree();
    });

    it('should switch to code view mode', () => {
      component.viewMode.set('diff');
      component.onFileSelected('src/main.ts');
      expect(component.viewMode()).toBe('code');
      // Drain the pending file-content request so httpMock.verify() passes.
      const req = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/file'
      );
      req.flush(makeFileContent({ path: 'src/main.ts' }));
    });

    it('should fire getFileContent via the real WorkspaceService', () => {
      component.onFileSelected('src/main.ts');

      const req = httpMock.expectOne(
        (r) =>
          r.url === '/api/workspace/test-project-id/file' &&
          r.params.get('path') === 'src/main.ts'
      );
      expect(req.request.method).toBe('GET');
      req.flush(makeFileContent({ path: 'src/main.ts' }));
    });

    it('should swallow HTTP errors silently (no rethrow)', () => {
      component.onFileSelected('src/main.ts');

      const req = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/file'
      );
      // Flush an error — the component's error handler should catch it.
      expect(() =>
        req.flush('boom', { status: 500, statusText: 'Server Error' })
      ).not.toThrow();
    });
  });

  // ── 4) onSelectCode — view toggle, no HTTP ────────────────────

  describe('onSelectCode', () => {
    beforeEach(() => {
      fixture.detectChanges();
      flushInitialTree();
    });

    it('should switch viewMode to code without HTTP calls', () => {
      component.viewMode.set('diff');

      component.onSelectCode();

      expect(component.viewMode()).toBe('code');
      httpMock.expectNone(
        (r) => r.url.includes('/api/workspace/test-project-id')
      );
    });
  });

  // ── 5) onSelectDiff — diff-before-switch invariant ────────────

  describe('onSelectDiff', () => {
    beforeEach(() => {
      fixture.detectChanges();
      flushInitialTree();
    });

    it('should switch to diff view WITHOUT HTTP when no path is selected', () => {
      expect(component.viewMode()).toBe('code');

      component.onSelectDiff();

      expect(component.viewMode()).toBe('diff');
      httpMock.expectNone(
        (r) => r.url.includes('/api/workspace/test-project-id/diff')
      );
    });

    it('should fire getFileDiff via the real WorkspaceService when a path is selected', () => {
      setSelectedPath('src/main.ts');

      component.onSelectDiff();

      const req = httpMock.expectOne(
        (r) =>
          r.url === '/api/workspace/test-project-id/diff' &&
          r.params.get('path') === 'src/main.ts'
      );
      expect(req.request.method).toBe('GET');
      req.flush(makeDiff({ path: 'src/main.ts' }));
    });

    it('should fetch diff BEFORE switching view mode (diff-before-switch)', () => {
      // Use a Subject so we can observe the in-between state.
      const pending = new Subject<GitDiffResponse>();
      jest
        .spyOn(workspaceService, 'getFileDiff')
        .mockReturnValue(pending.asObservable());

      setSelectedPath('src/main.ts');
      expect(component.viewMode()).toBe('code');

      component.onSelectDiff();

      // View mode MUST still be 'code' until the diff response lands.
      expect(component.viewMode()).toBe('code');
      expect(workspaceService.getFileDiff).toHaveBeenCalledWith(
        'test-project-id',
        'src/main.ts'
      );

      // Once the response arrives, the view flips to 'diff'.
      pending.next(makeDiff());
      pending.complete();
      expect(component.viewMode()).toBe('diff');
    });

    it('should fall back to diff view when the diff request errors', () => {
      const pending = new Subject<GitDiffResponse>();
      jest
        .spyOn(workspaceService, 'getFileDiff')
        .mockReturnValue(pending.asObservable());

      setSelectedPath('src/main.ts');

      component.onSelectDiff();

      expect(component.viewMode()).toBe('code');

      pending.error(new Error('boom'));

      expect(component.viewMode()).toBe('diff');
    });

    it('should still call the real HTTP service when no Subject spy is installed', () => {
      setSelectedPath('src/main.ts');

      component.onSelectDiff();

      // viewMode must not flip until we flush the HTTP response.
      expect(component.viewMode()).toBe('code');

      const req = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/diff'
      );
      req.flush(makeDiff({ path: 'src/main.ts' }));

      expect(component.viewMode()).toBe('diff');
    });
  });

  // ── 6) selectedPath signal mirror ─────────────────────────────

  describe('selectedPath signal', () => {
    it('should mirror WorkspaceService.selectedPath', () => {
      fixture.detectChanges();
      flushInitialTree();

      expect(component.selectedPath()).toBeNull();

      setSelectedPath('src/foo.py');
      expect(component.selectedPath()).toBe('src/foo.py');

      setSelectedPath(null);
      expect(component.selectedPath()).toBeNull();
    });
  });

  // ── 7) @Input() projectId (overlay mode) ─────────────────────────

  describe('@Input() projectId (overlay mode)', () => {
    let hostFixture: ComponentFixture<WorkspaceHostComponent>;
    let host: WorkspaceHostComponent;
    let workspace: WorkspaceComponent;
    let localHttp: HttpTestingController;
    let localService: WorkspaceService;

    beforeEach(async () => {
      TestBed.resetTestingModule();
      StubEventSource.instances = [];
      await TestBed.configureTestingModule({
        imports: [WorkspaceHostComponent],
        providers: [
          provideHttpClient(),
          provideHttpClientTesting(),
          provideNoopAnimations(),
          WorkspaceService,
          // Route returns null — the input should win regardless.
          {
            provide: ActivatedRoute,
            useValue: { snapshot: { paramMap: { get: () => null } } },
          },
        ],
      }).compileComponents();

      hostFixture = TestBed.createComponent(WorkspaceHostComponent);
      host = hostFixture.componentInstance;
      localHttp = TestBed.inject(HttpTestingController);
      localService = TestBed.inject(WorkspaceService);

      // Drain the editor-preference request fired by the
      // WorkspaceService constructor.
      {
        const editorReq = localHttp.expectOne('/api/settings/editor');
        editorReq.flush({ editor: 'builtin' });
      }

      // Reach into the projected <app-workspace>.
      workspace = hostFixture.debugElement
        .query(By.directive(WorkspaceComponent))
        .componentInstance;
    });

    afterEach(() => {
      localHttp.verify();
    });

    function flushValidatedWorkdirLocal(projectId: string, folder: string = ''): void {
      const req = localHttp.expectOne(`/api/projects/${projectId}/vscode-folder`);
      req.flush({ folder });
    }

    function flushTree(projectId: string, body?: FileTreeResponse): void {
      const req = localHttp.expectOne(
        (r) => r.url === `/api/workspace/${projectId}/tree`
      );
      req.flush(
        body ?? makeTreeResponse({ project_id: projectId })
      );
      flushValidatedWorkdirLocal(projectId);
    }

    it('uses the @Input() value when no route param is present', () => {
      host.projectId = 'input-project';
      hostFixture.detectChanges();

      expect(workspace.projectId).toBe('input-project');
      flushTree('input-project');
    });

    it('loads the file tree for the input-driven project', () => {
      host.projectId = 'input-project';
      hostFixture.detectChanges();

      const req = localHttp.expectOne(
        (r) =>
          r.url === '/api/workspace/input-project/tree' &&
          r.params.get('path') === '.'
      );
      expect(req.request.method).toBe('GET');
      req.flush(makeTreeResponse({ project_id: 'input-project' }));
      flushValidatedWorkdirLocal('input-project');
    });

    it('reacts to a projectId change with a fresh HTTP fetch (cache miss)', () => {
      host.projectId = 'first-project';
      hostFixture.detectChanges();
      flushTree('first-project');

      // Drive the new value through both the host binding and the child
      // instance setter for reliability across Angular versions.
      host.projectId = 'second-project';
      const wsInstance = hostFixture.debugElement.query(
        By.directive(WorkspaceComponent)
      ).componentInstance as WorkspaceComponent;
      wsInstance.projectId = 'second-project';
      hostFixture.detectChanges();

      // Capture the second project's request via `match` BEFORE flushing
      // (flushed requests are removed from the pending queue). Verifies
      // the setter triggered a fresh HTTP fetch and not a cache hit.
      const pending = localHttp.match(
        (r) => r.url.endsWith('/tree') && r.params.get('path') === '.'
      );
      expect(pending.length).toBe(1);
      expect(pending[0].request.url).toBe(
        '/api/workspace/second-project/tree'
      );

      pending[0].flush(makeTreeResponse({ project_id: 'second-project' }));
      // Drain the validated-workdir request for the second project.
      flushValidatedWorkdirLocal('second-project');
      expect(wsInstance.projectId).toBe('second-project');
    });

    it('skips the tree HTTP when switching to a project that has cached state, but refetches the selected file content', () => {
      // Seed the cache directly so we can verify the component avoids
      // an HTTP /tree fetch on switch (Bug 2 fix: cache hit avoids
      // re-listing). Set selectedPath BEFORE saving so the snapshot
      // captures it (saveCurrentState reads live signal values).
      //
      // Bug 1 fix: a /file request IS expected — `currentFile` is
      // deliberately not cached, so the component must refetch the
      // previously-selected file's content after restore.
      // `selectedPath` is now a computed derived from `_activeFilePath`;
      // set the underlying signal directly so the snapshot captures it
      // before saveCurrentState reads live signal values.
      (localService as unknown as { _activeFilePath: { set: (v: string | null) => void } })
        ._activeFilePath.set('src/cached.ts');
      localService.saveCurrentState('cached-project', { viewMode: 'diff' });

      host.projectId = 'cached-project';
      hostFixture.detectChanges();

      // No /tree HTTP request should have been issued.
      localHttp.expectNone(
        (r) => r.url === '/api/workspace/cached-project/tree'
      );

      // Cached state should have been applied to the component.
      expect(workspace.viewMode()).toBe('diff');
      expect(workspace.selectedPath()).toBe('src/cached.ts');

      // Bug 1 — a /file GET must be in-flight to refetch the content.
      const fileReq = localHttp.expectOne(
        (r) =>
          r.url === '/api/workspace/cached-project/file' &&
          r.params.get('path') === 'src/cached.ts'
      );
      expect(fileReq.request.method).toBe('GET');
      fileReq.flush(makeFileContent({ path: 'src/cached.ts' }));

      // The validated-workdir request still fires on the cache-hit
      // path so the VS Code iframe can open the right folder.
      flushValidatedWorkdirLocal('cached-project');
    });

    it('does NOT regress to the route value when the input is set', () => {
      // Even with a stale route param, the input wins.
      host.projectId = 'from-input';
      hostFixture.detectChanges();

      expect(workspace.projectId).toBe('from-input');
      flushTree('from-input');
    });
  });

  // ── 8) @Output() hide event ──────────────────────────────────────

  describe('@Output() hide', () => {
    let hostFixture: ComponentFixture<WorkspaceHostComponent>;
    let host: WorkspaceHostComponent;
    let localHttp: HttpTestingController;
    let localService: WorkspaceService;

    beforeEach(async () => {
      TestBed.resetTestingModule();
      StubEventSource.instances = [];
      await TestBed.configureTestingModule({
        imports: [WorkspaceHostComponent],
        providers: [
          provideHttpClient(),
          provideHttpClientTesting(),
          provideNoopAnimations(),
          WorkspaceService,
          {
            provide: ActivatedRoute,
            useValue: { snapshot: { paramMap: { get: () => 'route-project' } } },
          },
        ],
      }).compileComponents();

      hostFixture = TestBed.createComponent(WorkspaceHostComponent);
      host = hostFixture.componentInstance;
      localHttp = TestBed.inject(HttpTestingController);
      localService = TestBed.inject(WorkspaceService);

      // Drain the editor-preference request fired by the
      // WorkspaceService constructor.
      const editorReq = localHttp.expectOne('/api/settings/editor');
      editorReq.flush({ editor: 'builtin' });

      // Explicitly drive editor mode to builtin so that toolbar/tabs
      // assertions are mode-correct without relying on default flush.
      localService.setEditorMode('builtin');

      host.projectId = 'route-project';
      hostFixture.detectChanges();

      // Drain the initial tree request so httpMock.verify() passes later.
      const req = localHttp.expectOne(
        (r) => r.url === '/api/workspace/route-project/tree'
      );
      req.flush(makeTreeResponse({ project_id: 'route-project' }));

      // Drain the validated-workdir request for the route project.
      const workdirReq = localHttp.expectOne(
        '/api/projects/route-project/vscode-folder'
      );
      workdirReq.flush({ folder: '' });
    });

    afterEach(() => {
      localHttp.verify();
    });

    it('exposes a hide EventEmitter on the component', () => {
      const workspaceDebug = hostFixture.debugElement.query(
        By.directive(WorkspaceComponent)
      );
      const ws = workspaceDebug.componentInstance;
      expect(ws.hide).toBeDefined();
      expect(typeof ws.hide.emit).toBe('function');
    });

    it('renders a Hide button in the toolbar', () => {
      const hideBtn = hostFixture.debugElement.query(
        By.css('[data-testid="workspace-hide"]')
      );
      expect(hideBtn).not.toBeNull();
      expect(hideBtn.nativeElement.getAttribute('aria-label')).toBe(
        'Hide workspace'
      );
    });

    it('emits hide when the Hide button is clicked', () => {
      expect(host.hideCount).toBe(0);

      const hideBtn = hostFixture.debugElement.query(
        By.css('[data-testid="workspace-hide"]')
      );
      hideBtn.nativeElement.click();
      hostFixture.detectChanges();

      expect(host.hideCount).toBe(1);

      hideBtn.nativeElement.click();
      hostFixture.detectChanges();

      expect(host.hideCount).toBe(2);
    });
  });

  // ── 9) Save button — canSave, saveFile, snackbar, Ctrl+S ───────

  describe('Save button', () => {
    /** Flush the initial tree request fired by ngOnInit. */
    function bootWithTree(): void {
      fixture.detectChanges();
      flushInitialTree();
    }

    it('hides the save button when no file is selected', () => {
      bootWithTree();

      const trigger = fixture.debugElement.query(
        By.css('[data-testid="save-button"]')
      );
      expect(trigger).toBeNull();
    });

    it('renders the save button after a file is selected', () => {
      bootWithTree();
      selectFile();

      const trigger = fixture.debugElement.query(
        By.css('[data-testid="save-button"]')
      );
      expect(trigger).not.toBeNull();
    });

    it('does not render the dirty indicator when content is clean', () => {
      bootWithTree();
      selectFile();

      const indicator = fixture.debugElement.query(
        By.css('[data-testid="dirty-indicator"]')
      );
      expect(indicator).toBeNull();
    });

    it('renders the dirty indicator after the editor content changes', () => {
      bootWithTree();
      selectFile();
      markDirty();

      const indicator = fixture.debugElement.query(
        By.css('[data-testid="dirty-indicator"]')
      );
      expect(indicator).not.toBeNull();
      expect(indicator.nativeElement.textContent.trim()).toBe('*');
      expect(indicator.nativeElement.getAttribute('aria-label')).toBe(
        'Unsaved changes'
      );
    });

    describe('canSave()', () => {
      it('is false when no file is selected', () => {
        bootWithTree();
        expect(component.canSave()).toBe(false);
      });

      it('is false when a file is selected but content is not dirty', () => {
        bootWithTree();
        selectFile();

        expect(component.canSave()).toBe(false);
      });

      it('is true when a file is selected and content is dirty', () => {
        bootWithTree();
        selectFile();
        markDirty();

        expect(component.canSave()).toBe(true);
      });

      it('is false when the view mode is diff even if content is dirty', () => {
        bootWithTree();
        selectFile();
        markDirty();

        // Simulate switching to diff view — the code viewer is still
        // dirty in the background, but the save button should never
        // enable save against a diff view.
        component.viewMode.set('diff');
        fixture.detectChanges();

        expect(component.canSave()).toBe(false);
      });

      // ── F3 — binary files are read-only, Save must be disabled.
      // The CodeViewerComponent's effect clears editedContent on entry
      // to a binary file, so isDirty() is false; we also assert the
      // canSave() binary guard at the workspace layer for defence in
      // depth.
      it('is false when the current file is binary (F3)', () => {
        bootWithTree();
        selectFile('image.png');

        // Drive the file signal directly to a binary record.
        setCurrentFile(
          makeFileContent({
            path: 'image.png',
            content: '',
            language: null,
            total_lines: 0,
            binary: true,
            size_bytes: 1024,
          })
        );

        // Bypass the editor's read-only guard and force isDirty=true to
        // prove canSave() refuses on the binary flag alone.
        const codeViewerDebug = fixture.debugElement.query(
          By.directive(CodeViewerComponent)
        );
        const codeViewer = codeViewerDebug.componentInstance as CodeViewerComponent;
        codeViewer.editedContent.set('dirty');
        // originalContent stays at '' so isDirty() === true.
        expect(codeViewer.isDirty()).toBe(true);

        expect(component.canSave()).toBe(false);
      });

      // ── F4 — truncated files are read-only, Save must be disabled.
      it('is false when the current file is truncated (F4)', () => {
        bootWithTree();
        selectFile('big.txt');

        setCurrentFile(
          makeFileContent({
            path: 'big.txt',
            content: 'partial…',
            language: 'plaintext',
            total_lines: 1000,
            truncated: true,
            size_bytes: 1_048_576,
          })
        );

        const codeViewerDebug = fixture.debugElement.query(
          By.directive(CodeViewerComponent)
        );
        const codeViewer = codeViewerDebug.componentInstance as CodeViewerComponent;
        codeViewer.editedContent.set('dirty');
        expect(codeViewer.isDirty()).toBe(true);

        expect(component.canSave()).toBe(false);
      });

      // ── F7 — saving flag must be honoured by canSave().
      it('is false while a save is in flight (F7)', () => {
        bootWithTree();
        selectFile();
        markDirty();
        expect(component.canSave()).toBe(true);

        component.saving.set(true);
        expect(component.canSave()).toBe(false);
      });
    });

    describe('saveFile()', () => {
      it('is a no-op when no file is selected', () => {
        bootWithTree();
        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const snackBar = TestBed.inject(MatSnackBar);
        const openSpy = jest.spyOn(snackBar, 'open');

        component.saveFile();

        expect(saveFileSpy).not.toHaveBeenCalled();
        expect(openSpy).not.toHaveBeenCalled();
        httpMock.expectNone(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
      });

      it('is a no-op when the file is not dirty', () => {
        bootWithTree();
        selectFile();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const snackBar = TestBed.inject(MatSnackBar);
        const openSpy = jest.spyOn(snackBar, 'open');

        component.saveFile();

        expect(saveFileSpy).not.toHaveBeenCalled();
        expect(openSpy).not.toHaveBeenCalled();
      });

      it('PUTs the current content to the file endpoint with correct params', () => {
        bootWithTree();
        selectFile('src/main.ts');
        markDirty();

        component.saveFile();

        const req = httpMock.expectOne(
          (r) =>
            r.url === '/api/workspace/test-project-id/file' &&
            r.method === 'PUT'
        );
        expect(req.request.body).toEqual({
          path: 'src/main.ts',
          content: 'export const modified = true;',
        });
        req.flush(makeWriteResponse({ path: 'src/main.ts', size_bytes: 32 }));
      });

      it('shows success snackbar after the save resolves', () => {
        bootWithTree();
        selectFile();
        markDirty();

        // Use the component's own injector so the spy targets the same
        // MatSnackBar instance the component holds via `inject()`.
        const snackBar = fixture.debugElement.injector.get(MatSnackBar);
        const openSpy = jest.spyOn(snackBar, 'open');

        component.saveFile();
        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush(makeWriteResponse());

        expect(openSpy).toHaveBeenCalledWith(
          'File saved',
          'Dismiss',
          expect.objectContaining({ duration: 3000 })
        );
      });

      it('shows error snackbar when the save errors', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const snackBar = fixture.debugElement.injector.get(MatSnackBar);
        const openSpy = jest.spyOn(snackBar, 'open');

        component.saveFile();
        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush('boom', { status: 500, statusText: 'Server Error' });

        // F8 — status-mapped message; not the old generic "Failed to save file".
        // Uses `statusText` rather than `message` so the snackbar avoids
        // Angular's verbose "Http failure response for /…" prefix.
        expect(openSpy).toHaveBeenCalledWith(
          'Failed to save file: 500 Server Error',
          'Dismiss',
          expect.objectContaining({ duration: 5000 })
        );
      });

      // ── F8 — status-code-specific snackbar messages ──────────────
      // The component owns the status → message mapping so the snackbar
      // is the single error presentation; the service deliberately does
      // NOT set its error signal for save failures.
      it.each([
        [413, 'File too large'],
        [403, 'Permission denied'],
        [404, 'Project or file not found'],
        [0, 'Network error — check connection'],
      ])(
        'maps HTTP status %i to "%s" in the error snackbar',
        (status, expectedMessage) => {
          bootWithTree();
          selectFile();
          markDirty();

          const snackBar = fixture.debugElement.injector.get(MatSnackBar);
          const openSpy = jest.spyOn(snackBar, 'open');

          component.saveFile();
          const req = httpMock.expectOne(
            (r) => r.url === '/api/workspace/test-project-id/file'
          );
          req.flush('boom', { status, statusText: 'X' });

          expect(openSpy).toHaveBeenCalledWith(
            expectedMessage,
            'Dismiss',
            expect.objectContaining({ duration: 5000 })
          );
        }
      );

      // ── F7 — in-flight save guard ────────────────────────────────
      // After saveFile() is called but before the PUT resolves,
      // canSave() must be false and a second saveFile() call must NOT
      // fire a concurrent PUT.
      it('blocks concurrent saveFile calls while a save is in flight (F7)', () => {
        bootWithTree();
        selectFile();
        markDirty();

        // Use a Subject so the PUT stays pending.
        const pending = new Subject<FileWriteResponse>();
        jest
          .spyOn(workspaceService, 'saveFile')
          .mockReturnValue(pending.asObservable());

        expect(component.saving()).toBe(false);
        component.saveFile();
        expect(component.saving()).toBe(true);
        expect(workspaceService.saveFile).toHaveBeenCalledTimes(1);

        // canSave must reflect the in-flight state.
        expect(component.canSave()).toBe(false);

        // A second saveFile() call must be a no-op — no extra PUT.
        component.saveFile();
        expect(workspaceService.saveFile).toHaveBeenCalledTimes(1);

        // Ctrl+S while saving must also be a no-op.
        const ctrlS = new KeyboardEvent('keydown', {
          key: 's',
          ctrlKey: true,
          bubbles: true,
        });
        const preventDefaultSpy = jest.spyOn(ctrlS, 'preventDefault');
        component.onSaveKeydown(ctrlS);
        expect(workspaceService.saveFile).toHaveBeenCalledTimes(1);

        // Resolving the PUT must clear the saving flag.
        pending.next({
          project_id: 'test-project-id',
          path: 'src/main.ts',
          size_bytes: 1,
          saved: true,
        });
        pending.complete();
        expect(component.saving()).toBe(false);
        expect(component.canSave()).toBe(false); // also not dirty anymore
      });

      it('clears saving on error so the user can retry (F7 finalize on both paths)', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const pending = new Subject<FileWriteResponse>();
        jest
          .spyOn(workspaceService, 'saveFile')
          .mockReturnValue(pending.asObservable());

        component.saveFile();
        expect(component.saving()).toBe(true);

        pending.error(new Error('boom'));
        expect(component.saving()).toBe(false);
        expect(component.canSave()).toBe(true); // still dirty, can retry
      });

      it('exposes the saving signal for the save button binding (F7)', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const pending = new Subject<FileWriteResponse>();
        jest
          .spyOn(workspaceService, 'saveFile')
          .mockReturnValue(pending.asObservable());

        // The save button's `@if (saving()) { … } @else { … }` binding
        // reads this signal — assert the signal flips correctly so the
        // template binding renders the expected icon.
        expect(component.saving()).toBe(false);

        component.saveFile();
        fixture.detectChanges();
        expect(component.saving()).toBe(true);

        pending.next({
          project_id: 'test-project-id',
          path: 'src/main.ts',
          size_bytes: 1,
          saved: true,
        });
        pending.complete();
        fixture.detectChanges();
        expect(component.saving()).toBe(false);
      });

      // ── F2 — successful save aligns the saved-state baseline ─────
      // After a successful save, codeViewer.markSaved() is called so
      // isDirty() becomes false and a follow-up SSE push (the round-trip
      // of our own save) is allowed to refresh the editor.
      it('calls codeViewer.markSaved() after a successful save (F2)', () => {
        bootWithTree();
        selectFile();
        const codeViewer = markDirty();
        const markSavedSpy = jest.spyOn(codeViewer, 'markSaved');

        component.saveFile();
        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush(makeWriteResponse());

        expect(markSavedSpy).toHaveBeenCalledTimes(1);
        expect(codeViewer.isDirty()).toBe(false);
      });

      it('markSaved() receives the PUT body captured at saveFile entry, not a re-read after response', () => {
        bootWithTree();
        selectFile();
        const codeViewer = markDirty();
        const capturedAtSave = codeViewer.currentContent();
        const markSavedSpy = jest.spyOn(codeViewer, 'markSaved');

        component.saveFile();
        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );

        // Simulate the user typing more between the PUT departing and
        // the response landing. The markSaved baseline must reflect the
        // already-PUT body, not the new edits — so the file stays
        // dirty for the additional keystrokes.
        codeViewer.editedContent.set('export const modified = true!\n// and more');
        req.flush(makeWriteResponse());

        expect(markSavedSpy).toHaveBeenCalledWith(capturedAtSave);
        expect(codeViewer.isDirty()).toBe(true);
      });

      it('routes through the real WorkspaceService.saveFile with the right args', () => {
        bootWithTree();
        selectFile('src/app.ts');
        const codeViewer = markDirty();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');

        component.saveFile();

        expect(saveFileSpy).toHaveBeenCalledWith(
          'test-project-id',
          'src/app.ts',
          codeViewer.currentContent()
        );

        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush(makeWriteResponse({ path: 'src/app.ts' }));
      });
    });

    describe('Ctrl/Cmd+S keyboard shortcut', () => {
      function keydown(init: Partial<KeyboardEventInit>): KeyboardEvent {
        // Tests don't need a real event with native preventDefault — but
        // we still spy on it so we can verify the browser default is
        // being suppressed exactly when we expect.
        return new KeyboardEvent('keydown', { bubbles: true, ...init });
      }

      it('saves and prevents default when Ctrl+S is pressed and the file is dirty', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const event = keydown({ key: 's', ctrlKey: true });
        const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

        component.onSaveKeydown(event);

        expect(preventDefaultSpy).toHaveBeenCalled();
        expect(saveFileSpy).toHaveBeenCalled();

        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush(makeWriteResponse());
      });

      it('saves when Cmd+S is pressed (macOS)', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const event = keydown({ key: 's', metaKey: true });

        component.onSaveKeydown(event);

        expect(saveFileSpy).toHaveBeenCalled();

        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush(makeWriteResponse());
      });

      it('prevents default but does NOT save when no file is selected', () => {
        bootWithTree();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const event = keydown({ key: 's', ctrlKey: true });
        const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

        component.onSaveKeydown(event);

        expect(preventDefaultSpy).toHaveBeenCalled();
        expect(saveFileSpy).not.toHaveBeenCalled();
      });

      it('prevents default but does NOT save when the file is not dirty', () => {
        bootWithTree();
        selectFile();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const event = keydown({ key: 's', ctrlKey: true });
        const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

        component.onSaveKeydown(event);

        expect(preventDefaultSpy).toHaveBeenCalled();
        expect(saveFileSpy).not.toHaveBeenCalled();
      });

      it('does nothing for plain "s" keypress without modifier', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const event = keydown({ key: 's' });
        const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

        component.onSaveKeydown(event);

        expect(preventDefaultSpy).not.toHaveBeenCalled();
        expect(saveFileSpy).not.toHaveBeenCalled();
      });

      it('does nothing for Ctrl+A or other Ctrl-modified keys', () => {
        bootWithTree();
        selectFile();
        markDirty();

        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        const event = keydown({ key: 'a', ctrlKey: true });
        const preventDefaultSpy = jest.spyOn(event, 'preventDefault');

        component.onSaveKeydown(event);

        expect(preventDefaultSpy).not.toHaveBeenCalled();
        expect(saveFileSpy).not.toHaveBeenCalled();
      });
    });

    // ── 10b) Multi-file tab integration ────────────────────────────
    // The FileTabsComponent's (tabClick) and (closeTab) outputs wire
    // into WorkspaceComponent.onTabClick / onTabClose, which delegate
    // to WorkspaceService.setActiveFile / closeFile. onFileSelected
    // routes through openFile so the file becomes a tab AND its
    // content is fetched in one call. Defined inside the Save-button
    // describe so it can reuse the `bootWithTree` helper.
    describe('multi-file tab integration', () => {
      it('onTabClick calls workspace.setActiveFile with the clicked path', () => {
        bootWithTree();
        selectFile('src/a.ts');
        const spy = jest.spyOn(workspaceService, 'setActiveFile');

        component.onTabClick('src/a.ts');

        expect(spy).toHaveBeenCalledWith('src/a.ts');
      });

      it('onTabClick also calls workspace.ensureTabContent to hydrate the tab', () => {
        bootWithTree();
        selectFile('src/a.ts');
        const spy = jest.spyOn(workspaceService, 'ensureTabContent');

        component.onTabClick('src/a.ts');

        // ensureTabContent is a no-op when the tab is already hydrated,
        // but it must still be called on every tab click so a tab
        // restored from cache with no content gets fetched.
        expect(spy).toHaveBeenCalledWith(
          'test-project-id',
          'src/a.ts'
        );
      });

      it('onTabClose calls workspace.closeFile with the closed path', () => {
        bootWithTree();
        selectFile('src/a.ts');
        const spy = jest.spyOn(workspaceService, 'closeFile');

        component.onTabClose('src/a.ts');

        expect(spy).toHaveBeenCalledWith('src/a.ts');
      });

      it('onTabClose also calls codeViewer.forgetTab to release per-path edit state', () => {
        bootWithTree();
        selectFile('src/a.ts');
        const codeViewer = fixture.debugElement.query(
          By.directive(CodeViewerComponent)
        ).componentInstance as CodeViewerComponent;
        const forgetTabSpy = jest.spyOn(codeViewer, 'forgetTab');

        component.onTabClose('src/a.ts');

        expect(forgetTabSpy).toHaveBeenCalledWith('src/a.ts');
      });

      it('onTabClose opens the shared confirmation dialog and cancels cleanly when dismissed', () => {
        bootWithTree();
        selectFile('src/a.ts');
        workspaceService.setFileDirty('src/a.ts', true);
        nextDialogResult = false;
        const closeFileSpy = jest.spyOn(workspaceService, 'closeFile');

        component.onTabClose('src/a.ts');

        expect(dialogOpenSpy).toHaveBeenCalledTimes(1);
        expect(dialogOpenSpy).toHaveBeenCalledWith(
          ConfirmDialogComponent,
          expect.objectContaining({
            width: '420px',
            panelClass: 'dark-modal-panel',
            data: {
              title: 'Discard Unsaved Changes',
              message: '"a.ts" has unsaved changes. Close and discard them?',
              confirmLabel: 'Discard',
              cancelLabel: 'Cancel',
              destructive: true,
            },
          }),
        );
        expect(closeFileSpy).not.toHaveBeenCalled();
        expect(workspaceService.isTabOpen('src/a.ts')).toBe(true);
      });

      it('onTabClose closes a dirty tab only after the user confirms', () => {
        bootWithTree();
        selectFile('src/a.ts');
        workspaceService.setFileDirty('src/a.ts', true);
        nextDialogResult = true;
        const closeFileSpy = jest.spyOn(workspaceService, 'closeFile');

        component.onTabClose('src/a.ts');

        expect(dialogOpenSpy).toHaveBeenCalledTimes(1);
        expect(closeFileSpy).toHaveBeenCalledWith('src/a.ts');
        expect(workspaceService.isTabOpen('src/a.ts')).toBe(false);
      });

      it('onTabClose skips the confirmation dialog when the tab is not dirty', () => {
        bootWithTree();
        selectFile('src/b.ts');
        const closeFileSpy = jest.spyOn(workspaceService, 'closeFile');

        component.onTabClose('src/b.ts');

        expect(dialogOpenSpy).not.toHaveBeenCalled();
        expect(closeFileSpy).toHaveBeenCalledWith('src/b.ts');
      });

      it('onFileSelected routes through workspace.openFile', () => {
        bootWithTree();
        const openFileSpy = jest.spyOn(workspaceService, 'openFile');

        component.onFileSelected('src/new.ts');

        // openFile is the tab-opening entry point. The component
        // does NOT also call getFileContent directly (which would
        // fire a duplicate HTTP request); openFile handles the
        // content fetch internally.
        expect(openFileSpy).toHaveBeenCalledWith(
          'test-project-id',
          'src/new.ts'
        );

        // Drain the openFile-driven HTTP request.
        const req = httpMock.expectOne(
          (r) => r.url === '/api/workspace/test-project-id/file'
        );
        req.flush(makeFileContent({ path: 'src/new.ts' }));
      });

      it('save flow PUTs the active tab path (not the previously selected file)', () => {
        // Open two files in turn. The second becomes the active tab.
        bootWithTree();
        selectFile('src/first.ts');
        markDirty();
        selectFile('src/second.ts');
        markDirty();

        // Save — the PUT must target the second (active) file.
        const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
        component.saveFile();

        expect(saveFileSpy).toHaveBeenCalledWith(
          'test-project-id',
          'src/second.ts',
          expect.any(String)
        );

        // Drain the PUT request.
        const req = httpMock.expectOne(
          (r) =>
            r.url === '/api/workspace/test-project-id/file' &&
            r.method === 'PUT'
        );
        req.flush(makeWriteResponse({ path: 'src/second.ts' }));
      });

      it('shows an empty state when no file is active and hides the code viewer', () => {
        bootWithTree();
        // No file selected — workspace.activeFilePath() is null.
        expect(workspaceService.activeFilePath()).toBeNull();

        // The code viewer must NOT be in the DOM.
        const codeViewer = fixture.debugElement.query(
          By.directive(CodeViewerComponent)
        );
        expect(codeViewer).toBeNull();

        // The empty-state placeholder must be present.
        const emptyState = fixture.debugElement.query(
          By.css('[data-testid="workspace-empty-state"]')
        );
        expect(emptyState).not.toBeNull();
      });
    });
  });

  // ── 11) Tab switch state preservation (Bug 1 + Bug 2 regression) ──
  // Round-trip A → B → A must:
  //   - re-fetch the file content for A's previously-selected file
  //     (Bug 1 — currentFile is intentionally not cached; refetch only),
  //   - re-apply A's tree expansion state (Bug 2 — the component must
  //     capture getExpandedPaths() before switching and pass them through
  //     to restoreState as outgoingUiExtras).
  describe('Tab switch state preservation (Bug 1 + Bug 2)', () => {
    let hostFixture: ComponentFixture<WorkspaceHostComponent>;
    let host: WorkspaceHostComponent;
    let localHttp: HttpTestingController;
    let localService: WorkspaceService;
    let workspace: WorkspaceComponent;

    beforeEach(async () => {
      TestBed.resetTestingModule();
      StubEventSource.instances = [];
      await TestBed.configureTestingModule({
        imports: [WorkspaceHostComponent],
        providers: [
          provideHttpClient(),
          provideHttpClientTesting(),
          provideNoopAnimations(),
          WorkspaceService,
          {
            provide: ActivatedRoute,
            useValue: { snapshot: { paramMap: { get: () => null } } },
          },
        ],
      }).compileComponents();

      hostFixture = TestBed.createComponent(WorkspaceHostComponent);
      host = hostFixture.componentInstance;
      localHttp = TestBed.inject(HttpTestingController);
      localService = TestBed.inject(WorkspaceService);

      // Drain the editor-preference request fired by the
      // WorkspaceService constructor.
      const editorReq = localHttp.expectOne('/api/settings/editor');
      editorReq.flush({ editor: 'builtin' });

      workspace = hostFixture.debugElement
        .query(By.directive(WorkspaceComponent))
        .componentInstance as WorkspaceComponent;
    });

    afterEach(() => {
      localHttp.verify();
    });

    /** Drain the pending /tree GET request for `projectId`. */
    function flushTree(projectId: string, body?: FileTreeResponse): void {
      const req = localHttp.expectOne(
        (r) => r.url === `/api/workspace/${projectId}/tree`
      );
      req.flush(body ?? makeTreeResponse({ project_id: projectId }));
      // Drain the validated-workdir request fired alongside the tree
      // fetch by `loadValidatedWorkdir`.
      flushValidatedWorkdirLocal(projectId);
    }

    /**
     * Drain the C2 `GET /api/projects/{id}/vscode-folder` request that
     * `loadValidatedWorkdir` fires when `loadProject` is called.
     */
    function flushValidatedWorkdirLocal(projectId: string, folder: string = ''): void {
      const req = localHttp.expectOne(`/api/projects/${projectId}/vscode-folder`);
      req.flush({ folder });
    }

    /** Drain the pending /file GET request for `projectId`. */
    function flushFile(
      projectId: string,
      path: string,
      body?: FileContentResponse
    ): FileContentResponse {
      const req = localHttp.expectOne(
        (r) =>
          r.url === `/api/workspace/${projectId}/file` &&
          r.params.get('path') === path
      );
      const response = body ?? makeFileContent({ path, project_id: projectId });
      req.flush(response);
      return response;
    }

    function setProjectId(value: string): void {
      // Drive the binding both ways — through the host property AND the
      // child input setter — to mirror what Angular does on prop change.
      host.projectId = value;
      workspace.projectId = value;
      hostFixture.detectChanges();
    }

    /**
     * Bug 1 regression: switching projects A → B → A must re-fetch the
     * file content for A's previously-selected file. Before the fix,
     * `restoreState` set `currentFile` to null with no refetch, so the
     * viewer showed an empty state after returning to A.
     */
    it('A → B → A: refetches file content for A after returning from B', () => {
      // 1. Mount project A — flush initial tree, then select a file.
      setProjectId('project-a');
      flushTree('project-a');
      workspace.onFileSelected('src/main.ts');
      const initialContent = flushFile(
        'project-a',
        'src/main.ts',
        makeFileContent({
          path: 'src/main.ts',
          project_id: 'project-a',
          content: '// A file content',
        })
      );
      expect(workspace.currentFile()).toEqual(initialContent);
      expect(workspace.selectedPath()).toBe('src/main.ts');

      // 2. Switch to project B (cache miss — fresh tree fetch).
      setProjectId('project-b');
      flushTree('project-b');
      // A is now cached with selectedPath = 'src/main.ts' (from step 1)
      // and currentFile intentionally NOT cached.
      expect(localService.hasCachedState('project-a')).toBe(true);

      // 3. Switch back to A (cache hit) — must fire getFileContent
      //    because the LRU cache intentionally omits file content.
      setProjectId('project-a');

      // The refetch is async; expectOne drains the request from the
      // queue, so we use it directly (match() also drains in Angular's
      // HttpTestingController, so combining the two would lose the
      // request between calls).
      const refetched = flushFile(
        'project-a',
        'src/main.ts',
        makeFileContent({
          path: 'src/main.ts',
          project_id: 'project-a',
          content: '// A file content (fresh)',
        })
      );

      // Drain the validated-workdir request that fires on the
      // cache-hit path so the iframe can open the right folder.
      flushValidatedWorkdirLocal('project-a');

      // currentFile is now re-populated with the refetched content.
      expect(workspace.currentFile()).toEqual(refetched);
      expect(workspace.selectedPath()).toBe('src/main.ts');
    });

    /**
     * Bug 1 defence-in-depth: when restoring a project that has no
     * previously-selected file, the component must NOT fire a phantom
     * `getFileContent` request. Without this guard, every project switch
     * to a cached project would issue a spurious HTTP request.
     */
    it('A → B → A: does NOT refetch content when A had no file selected', () => {
      // 1. Mount A. Select nothing.
      setProjectId('project-a');
      flushTree('project-a');
      expect(workspace.selectedPath()).toBeNull();

      // 2. Switch to B (cache miss).
      setProjectId('project-b');
      flushTree('project-b');

      // 3. Switch back to A (cache hit) — must NOT fire getFileContent.
      setProjectId('project-a');

      localHttp.expectNone(
        (r) => r.url === '/api/workspace/project-a/file'
      );
      // The validated-workdir request still fires on the cache-hit
      // path so the iframe can open the right folder.
      flushValidatedWorkdirLocal('project-a');
      expect(workspace.currentFile()).toBeNull();
      expect(workspace.selectedPath()).toBeNull();
    });

    /**
     * Bug 2 regression: switching projects A → B → A must preserve A's
     * expanded tree paths. Before the fix, `getExpandedPaths()` was
     * never threaded into the outgoing save on a cache miss, so the
     * outgoing snapshot always had `expandedPaths: []` and the restore
     * gate `if (restored.expandedPaths.length > 0)` never passed.
     */
    it('A → B → A: preserves A\'s expanded directories after returning from B', () => {
      // 1. Mount A. Seed the FileTreeComponent's expanded set so
      //    getExpandedPaths() returns the expected values.
      setProjectId('project-a');
      flushTree('project-a');

      const fileTreeDebug = hostFixture.debugElement.query(
        By.directive(FileTreeComponent)
      );
      const fileTreeA = fileTreeDebug.componentInstance as FileTreeComponent;
      fileTreeA.restoreExpandedPaths(['src', 'src/components']);
      expect(fileTreeA.getExpandedPaths()).toEqual([
        'src',
        'src/components',
      ]);

      // 2. Switch to B — this is a cache MISS for B, so the
      //    WorkspaceComponent saves outgoing UI extras (A's expanded
      //    paths + viewMode) via `saveCurrentState('project-a', …)`
      //    BEFORE calling `getFileTree('project-b')`. That cache-miss
      //    capture is what makes A's expansion survive the round-trip;
      //    it is NOT captured by `restoreState` (the cache-hit path).
      setProjectId('project-b');
      flushTree('project-b');

      // A's cached snapshot must now include the expanded paths the
      // component captured right before getFileTree. `peekCachedState`
      // is side-effect-free — no signals reset, no MRU reordering —
      // so this is a pure assertion on what the cache currently holds.
      expect(localService.peekCachedState('project-a')?.expandedPaths).toEqual([
        'src',
        'src/components',
      ]);

      // 3. Switch back to A (cache hit) — the workspace applies A's
      //    cached tree via fileTree.setTree(), which (per the current
      //    setTree contract) clears the live expanded set to [] before
      //    assigning new data. The final non-empty expandedPaths below
      //    can therefore ONLY come from `restoreExpandedPaths` being
      //    called with A's cached paths after restore — i.e. from the
      //    cached snapshot that the cache-miss capture above populated.
      setProjectId('project-a');

      // Drain the validated-workdir request that fires on the
      // cache-hit path so the iframe can open the right folder.
      flushValidatedWorkdirLocal('project-a');

      // After restore, the workspace sets the tree and (because
      // expandedPaths.length > 0) calls fileTree.restoreExpandedPaths.
      const fileTreeDebugAfter = hostFixture.debugElement.query(
        By.directive(FileTreeComponent)
      );
      const fileTreeAfter =
        fileTreeDebugAfter.componentInstance as FileTreeComponent;
      expect(fileTreeAfter.getExpandedPaths()).toEqual([
        'src',
        'src/components',
      ]);
    });

    /**
     * Bug 2 defence-in-depth: when the outgoing project has no
     * expanded paths, the restore must not crash and the round-trip
     * must still work. This is the "tree never expanded" baseline.
     */
    it('A → B → A: works when no directories are expanded (empty array)', () => {
      setProjectId('project-a');
      flushTree('project-a');

      const fileTreeDebug = hostFixture.debugElement.query(
        By.directive(FileTreeComponent)
      );
      const fileTreeA = fileTreeDebug.componentInstance as FileTreeComponent;
      expect(fileTreeA.getExpandedPaths()).toEqual([]);

      // Switch to B then back to A.
      setProjectId('project-b');
      flushTree('project-b');

      setProjectId('project-a');
      // Drain the validated-workdir request that fires on the
      // cache-hit path so the iframe can open the right folder.
      flushValidatedWorkdirLocal('project-a');

      const fileTreeAfter = hostFixture.debugElement
        .query(By.directive(FileTreeComponent))
        .componentInstance as FileTreeComponent;
      expect(fileTreeAfter.getExpandedPaths()).toEqual([]);
    });
  });

  // ── 11) Multi-file tab integration ──────────────────────────────
  // (Tests live in the Save-button describe block above so they can
  // reuse the `bootWithTree` / `selectFile` / `markDirty` helpers.)

  // ── 12) Editor switching (Phase 5) ─────────────────────────────
  // The `@switch (editorMode())` block in the template renders either
  // the built-in CodeMirror viewer or the VS Code Server iframe. The
  // signal is owned by `WorkspaceService` and hydrated from
  // `/api/settings/editor` in the service constructor; the workspace
  // template forwards the validated workdir to the VS Code viewer.
  describe('editor switching', () => {
    beforeEach(() => {
      // Prime the editor mode BEFORE creating the component so the
      // initial template render reflects the requested mode. The
      // service's constructor fires `getEditorPreference`; this
      // helper rebuilds the TestBed with a controlled response.
    });

    function setupWithEditorMode(mode: 'builtin' | 'vscode'): void {
      // Reset and reconfigure TestBed so the WorkspaceService
      // constructor sees the response we want for the editor mode.
      const editorResponse = { editor: mode };
      const workdir = '/Users/test/projects/foo';
      TestBed.resetTestingModule();
      StubEventSource.instances = [];
      TestBed.configureTestingModule({
        imports: [WorkspaceComponent],
        providers: [
          provideHttpClient(),
          provideHttpClientTesting(),
          provideNoopAnimations(),
          WorkspaceService,
          {
            provide: ActivatedRoute,
            useValue: {
              snapshot: { paramMap: { get: () => 'test-project-id' } },
            },
          },
        ],
      });
      fixture = TestBed.createComponent(WorkspaceComponent);
      dialogOpenSpy = jest
        .spyOn(fixture.debugElement.injector.get(MatDialog), 'open')
        .mockReturnValue({
          afterClosed: () => of(nextDialogResult),
        } as any);
      component = fixture.componentInstance;
      httpMock = TestBed.inject(HttpTestingController);
      workspaceService = TestBed.inject(WorkspaceService);

      // Drain the editor-preference request with the desired mode.
      const editorReq = httpMock.expectOne('/api/settings/editor');
      editorReq.flush(editorResponse);

      // Prime the validated workdir so the VS Code viewer (if present)
      // sees a stable path.
      (workspaceService as unknown as {
        editorMode: { set: (v: 'builtin' | 'vscode') => void };
      }).editorMode.set(mode);
      if (mode === 'vscode') {
        // The signal is exposed via the component — prime it directly.
        component.validatedWorkdir.set(workdir);
      }
    }

    /** Render the workspace in one editor mode and drain its boot requests. */
    function renderWithEditorMode(mode: 'builtin' | 'vscode'): void {
      setupWithEditorMode(mode);
      fixture.detectChanges();

      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());

      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      workdirReq.flush({ folder: '/Users/test/projects/foo' });
      fixture.detectChanges();
    }

    it('renders the content toolbar and file tabs when editorMode="builtin"', () => {
      renderWithEditorMode('builtin');

      expect(
        fixture.debugElement.query(By.css('mat-toolbar.content-toolbar'))
      ).not.toBeNull();
      expect(
        fixture.debugElement.query(By.css('app-file-tabs'))
      ).not.toBeNull();
    });

    it('does not render the content toolbar or file tabs when editorMode="vscode"', () => {
      renderWithEditorMode('vscode');

      expect(
        fixture.debugElement.query(By.css('mat-toolbar.content-toolbar'))
      ).toBeNull();
      expect(
        fixture.debugElement.query(By.css('app-file-tabs'))
      ).toBeNull();
    });

    it('Ctrl+S is inert in vscode mode (no save, no preventDefault)', () => {
      // Set up VS Code mode. renderWithEditorMode drains the boot
      // requests but does NOT select a file or mark it dirty — that is
      // intentional: even with no file selected, the daemon handler
      // would normally still preventDefault. We want to assert that
      // the VS Code-mode guard fires first and bails out cleanly.
      renderWithEditorMode('vscode');

      const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
      const ctrlS = new KeyboardEvent('keydown', {
        bubbles: true,
        key: 's',
        ctrlKey: true,
      });
      const preventDefaultSpy = jest.spyOn(ctrlS, 'preventDefault');

      component.onSaveKeydown(ctrlS);

      expect(saveFileSpy).not.toHaveBeenCalled();
      // The handler must NOT swallow the keydown — we want the
      // browser/code-server default save to proceed.
      expect(preventDefaultSpy).not.toHaveBeenCalled();
    });

    it('Cmd+S is inert in vscode mode (macOS parity)', () => {
      renderWithEditorMode('vscode');

      const saveFileSpy = jest.spyOn(workspaceService, 'saveFile');
      const cmdS = new KeyboardEvent('keydown', {
        bubbles: true,
        key: 's',
        metaKey: true,
      });
      const preventDefaultSpy = jest.spyOn(cmdS, 'preventDefault');

      component.onSaveKeydown(cmdS);

      expect(saveFileSpy).not.toHaveBeenCalled();
      expect(preventDefaultSpy).not.toHaveBeenCalled();
    });

    it('Escape dismisses the workspace via onHide in vscode mode', () => {
      renderWithEditorMode('vscode');
      const onHideSpy = jest.spyOn(component, 'onHide');
      const hideEmitSpy = jest.spyOn(component.hide, 'emit');

      component.onEscapeKey();

      expect(onHideSpy).toHaveBeenCalledTimes(1);
      expect(hideEmitSpy).toHaveBeenCalledTimes(1);
    });

    it('Escape does NOT dismiss the workspace in builtin mode', () => {
      renderWithEditorMode('builtin');
      const onHideSpy = jest.spyOn(component, 'onHide');
      const hideEmitSpy = jest.spyOn(component.hide, 'emit');

      component.onEscapeKey();

      expect(onHideSpy).not.toHaveBeenCalled();
      expect(hideEmitSpy).not.toHaveBeenCalled();
    });

    it('editorMode="builtin" does NOT render the VsCodeViewerComponent', () => {
      setupWithEditorMode('builtin');
      fixture.detectChanges();
      // Drain the tree and workdir requests.
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());
      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      workdirReq.flush({ folder: '/Users/test/projects/foo' });
      fixture.detectChanges();

      // VS Code viewer must NOT be in the DOM in builtin mode.
      const vsCodeViewer = fixture.debugElement.query(
        By.css('app-vscode-viewer')
      );
      expect(vsCodeViewer).toBeNull();
      // The CodeMirror viewer is only rendered when a file is selected
      // (empty state otherwise). With no file selected, the empty-state
      // placeholder is rendered instead — assert that the @switch
      // case took the builtin branch.
      const emptyState = fixture.debugElement.query(
        By.css('[data-testid="workspace-empty-state"]')
      );
      expect(emptyState).not.toBeNull();
    });

    it('editorMode="builtin" renders the CodeViewerComponent when a file is selected', () => {
      setupWithEditorMode('builtin');
      fixture.detectChanges();
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());
      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      workdirReq.flush({ folder: '/Users/test/projects/foo' });

      // Select a file so the CodeViewer is rendered.
      component.onFileSelected('src/main.ts');
      const fileReq = httpMock.expectOne(
        (r) =>
          r.url === '/api/workspace/test-project-id/file' &&
          r.params.get('path') === 'src/main.ts'
      );
      fileReq.flush(makeFileContent({ path: 'src/main.ts' }));
      fixture.detectChanges();

      const codeViewer = fixture.debugElement.query(
        By.directive(CodeViewerComponent)
      );
      expect(codeViewer).not.toBeNull();
      const vsCodeViewer = fixture.debugElement.query(
        By.css('app-vscode-viewer')
      );
      expect(vsCodeViewer).toBeNull();
    });

    it('editorMode="vscode" renders the VsCodeViewerComponent', () => {
      setupWithEditorMode('vscode');
      fixture.detectChanges();
      // Drain the tree and workdir requests.
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());
      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      workdirReq.flush({ folder: '/Users/test/projects/foo' });
      fixture.detectChanges();

      // VS Code viewer must be in the DOM.
      const vsCodeViewer = fixture.debugElement.query(
        By.css('app-vscode-viewer')
      );
      expect(vsCodeViewer).not.toBeNull();
      // CodeMirror viewer must NOT be in the DOM.
      const codeViewer = fixture.debugElement.query(
        By.directive(CodeViewerComponent)
      );
      expect(codeViewer).toBeNull();
    });

    it('loadValidatedWorkdir fetches the validated folder endpoint', () => {
      setupWithEditorMode('vscode');
      fixture.detectChanges();
      // Drain the tree request.
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());

      // The validated workdir request uses the exact URL pattern
      // documented as the source of truth for the iframe folder.
      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      expect(workdirReq.request.method).toBe('GET');
      workdirReq.flush({ folder: '/Users/test/projects/foo' });
    });

    it('loadValidatedWorkdir sets validatedWorkdir signal on success', () => {
      setupWithEditorMode('vscode');
      fixture.detectChanges();
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());

      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      workdirReq.flush({ folder: '/Users/test/projects/foo' });

      expect(component.validatedWorkdir()).toBe('/Users/test/projects/foo');
    });

    it('loadValidatedWorkdir clears validatedWorkdir signal on error', () => {
      setupWithEditorMode('vscode');
      fixture.detectChanges();
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      treeReq.flush(makeTreeResponse());

      // Seed an initial value so we can prove the error path clears it.
      component.validatedWorkdir.set('/some/prior/value');

      const workdirReq = httpMock.expectOne(
        '/api/projects/test-project-id/vscode-folder'
      );
      workdirReq.flush('boom', { status: 500, statusText: 'Server Error' });

      expect(component.validatedWorkdir()).toBe('');
    });
  });

  // ── 13) Error banner (S5 regression) ──────────────────────────
  // Live e2e (tester r3, S5) proved that when the tree API fails
  // (e.g. project without main_directory → 400), the rendered
  // `.error-banner` stretched to fill the ENTIRE workspace flex
  // container (1280×664) and intercepted clicks on the project tab
  // bar beneath the overlay. The SCSS fix constrains the banner to a
  // content-height strip via a flex-column `.viewer-content`.
  //
  // Jest (jsdom) cannot do real layout, so the CSS contract is pinned
  // via source-text checks on the compiled stylesheet (precedent:
  // instance-detail.component.spec.ts) plus a DOM test asserting the
  // banner renders as a sibling BEFORE the viewer pane (so a
  // top-anchored strip cannot cover the pane) and stays dismissible.
  describe('error banner (S5 regression)', () => {
    /** Boot the workspace and fail the initial tree request (400). */
    function bootWithTreeError(): void {
      fixture.detectChanges();
      const treeReq = httpMock.expectOne(
        (r) => r.url === '/api/workspace/test-project-id/tree'
      );
      // Mirror the real backend envelope for a project without
      // main_directory: {"detail": {"error": "Project has no
      // main_directory configured"}}.
      treeReq.flush(
        { detail: { error: 'Project has no main_directory configured' } },
        { status: 400, statusText: 'Bad Request' }
      );
      flushValidatedWorkdir('test-project-id');
      fixture.detectChanges();
    }

    it('renders the error banner with the backend reason when the tree load fails', () => {
      bootWithTreeError();

      const banner = fixture.nativeElement.querySelector('.error-banner');
      expect(banner).not.toBeNull();
      const message = banner.querySelector('.error-message')?.textContent;
      expect(message).toContain('Project has no main_directory configured');
      expect(message).toContain('400');
    });

    it('renders the banner as a previous sibling of the viewer pane (top strip, not overlay)', () => {
      bootWithTreeError();

      const content = fixture.nativeElement.querySelector('.viewer-content');
      expect(content).not.toBeNull();
      const children = Array.from(content.children);
      const bannerIndex = children.findIndex((el) =>
        el.classList.contains('error-banner')
      );
      const paneIndex = children.findIndex((el) =>
        el.classList.contains('empty-state')
      );
      expect(bannerIndex).toBeGreaterThanOrEqual(0);
      expect(paneIndex).toBeGreaterThanOrEqual(0);
      // Banner comes FIRST in DOM order — with the flex-column layout
      // it occupies a strip at the top and the pane takes the rest.
      expect(bannerIndex).toBeLessThan(paneIndex);
    });

    it('dismiss button clears the banner from the DOM', () => {
      bootWithTreeError();

      const banner = fixture.nativeElement.querySelector('.error-banner');
      expect(banner).not.toBeNull();
      banner.querySelector('button[aria-label="Dismiss error"]')?.click();
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.error-banner')).toBeNull();
    });

    describe('SCSS contract (source-text)', () => {
      // jsdom applies no layout, so the geometry fix is pinned by
      // reading the stylesheet source — the same pattern used by
      // instance-detail.component.spec.ts for template invariants.
      const fs = require('fs') as typeof import('fs');
      const join = require('path') as typeof import('path');
      const scssPath = join.join(__dirname, 'workspace.component.scss');
      const scss = fs.readFileSync(scssPath, 'utf8');

      it('viewer-content is a flex column (banner + pane share vertical space)', () => {
        const match = scss.match(/\.viewer-content\s*\{[^}]*\}/);
        expect(match).not.toBeNull();
        expect(match![0]).toContain('display: flex');
        expect(match![0]).toContain('flex-direction: column');
      });

      it('error-banner is constrained to content height (no grow/shrink/stretch)', () => {
        const matches = scss.match(/\.error-banner\s*\{[^}]*\}/g);
        expect(matches).not.toBeNull();
        // The full rule set (there are two blocks: base + position).
        const all = matches!.join('\n');
        expect(all).toContain('flex: 0 0 auto');
        expect(all).toContain('height: auto');
      });

      it('no blanket `.viewer-content > *` full-height rule remains', () => {
        // The root cause: `.viewer-content > * { height: 100% }`
        // stretched EVERY child — including the banner — to the full
        // workspace height. Must not come back. Comments are stripped
        // first so prose mentioning the selector doesn't self-trip.
        const noComments = scss.replace(/\/\*[\s\S]*?\*\//g, '');
        expect(noComments).not.toMatch(/\.viewer-content\s*>\s*\*/);
      });

      it('viewer panes still fill the remaining space (flex grow, basis 0)', () => {
        // Single-rule window: `[^{]*\{[^}]*` keeps the flex declaration
        // pinned INSIDE the same rule block as the selector — the old
        // unbounded `[\s\S]*?` could satisfy itself from any later rule.
        expect(scss).toMatch(
          /\.viewer-content\s*>\s*app-code-viewer[^{]*\{[^}]*flex:\s*1\s+1\s+0/
        );
      });
    });
  });
});
