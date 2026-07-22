import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { ActivatedRoute } from '@angular/router';
import { By } from '@angular/platform-browser';
import { Subject } from 'rxjs';
import { WorkspaceComponent } from './workspace.component';
import { WorkspaceService } from '../../services/workspace.service';
import { FileTreeComponent } from '../../components/file-tree/file-tree.component';
import type {
  FileContentResponse,
  FileTreeResponse,
  GitDiffResponse,
} from '../../models/workspace.model';

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

  /**
   * Flush the initial `getFileTree` request that ngOnInit fires.
   * Tests that need a non-empty tree can override the response before
   * calling this helper.
   */
  function flushInitialTree(): void {
    const req = httpMock.expectOne(
      (r) => r.url === '/api/workspace/test-project-id/tree' && r.params.get('path') === '.'
    );
    req.flush(makeTreeResponse());
  }

  // ── TestBed setup ──────────────────────────────────────────────

  beforeEach(async () => {
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
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    workspaceService = TestBed.inject(WorkspaceService);
  });

  afterEach(() => {
    httpMock.verify();
  });

  // ── 1) Component creation ─────────────────────────────────────

  it('creates successfully', () => {
    expect(component).toBeTruthy();
  });

  // ── 2) ngOnInit extracts projectId and loads tree ─────────────

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
      workspaceService.selectedPath.set('src/main.ts');

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

      workspaceService.selectedPath.set('src/main.ts');
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

      workspaceService.selectedPath.set('src/main.ts');

      component.onSelectDiff();

      expect(component.viewMode()).toBe('code');

      pending.error(new Error('boom'));

      expect(component.viewMode()).toBe('diff');
    });

    it('should still call the real HTTP service when no Subject spy is installed', () => {
      workspaceService.selectedPath.set('src/main.ts');

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

      workspaceService.selectedPath.set('src/foo.py');
      expect(component.selectedPath()).toBe('src/foo.py');

      workspaceService.selectedPath.set(null);
      expect(component.selectedPath()).toBeNull();
    });
  });
});