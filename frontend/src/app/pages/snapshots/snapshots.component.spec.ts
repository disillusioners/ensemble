import { ComponentFixture, TestBed, fakeAsync, tick, flushMicrotasks } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { BehaviorSubject, of, throwError } from 'rxjs';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatSnackBar } from '@angular/material/snack-bar';
import { ActivatedRoute, Router, convertToParamMap } from '@angular/router';
import { ParamMap } from '@angular/router';
import { signal } from '@angular/core';

import { SnapshotsComponent } from './snapshots.component';
import { SnapshotService } from '../../services/snapshot.service';
import { ProjectService } from '../../services/project.service';
import { SettingsService } from '../../services/settings.service';
import { Project } from '../../models/project.model';
import {
  SnapshotFilters,
  SnapshotListResponse,
  SnapshotRow,
  SnapshotUsageMetrics,
} from '../../models/snapshot.model';

// ── Factories ────────────────────────────────────────────────

function makeRow(overrides: Partial<SnapshotRow> = {}): SnapshotRow {
  return {
    id: 'row-uuid-1',
    project_id: 'proj-uuid-1',
    created_by_agent_id: 'coder',
    target_instance_id: 'inst-uuid-1',
    title: 'first-snapshot',
    domain_tags: [],
    status: 'active',
    supersedes_snapshot_id: null,
    git_sha: 'a3c7d2e1f908',
    git_branch: 'feature/test',
    git_dirty: false,
    repo_path: '/tmp/repo',
    runtime_version: 'v0.17.0',
    effective_model: 'gpt-4o',
    created_at: '2026-10-05T18:54:12.000Z',
    ...overrides,
  };
}

function makeListResponse(overrides: Partial<SnapshotListResponse> = {}): SnapshotListResponse {
  return { items: [], total: 0, ...overrides };
}

function makeMetrics(overrides: Partial<SnapshotUsageMetrics> = {}): SnapshotUsageMetrics {
  return { capture_counts: {}, spawn_counts_per_snapshot: [], ...overrides };
}

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    project_id: 'proj-uuid-1',
    name: 'default',
    description: null,
    created_at: '2026-01-01T00:00:00Z',
    settings: {},
    is_default: false,
    job_queue_paused: false,
    ...overrides,
  } as Project;
}

// ── Route stubs (v2 AC-6.3 — URL queryParams mirror) ───────
function makeActivatedRouteStub(initialQp: Record<string, string | string[]> = {}): {
  snapshot: { queryParamMap: ParamMap };
  queryParamMap: BehaviorSubject<ParamMap>;
} {
  const subject = new BehaviorSubject<ParamMap>(convertToParamMap(initialQp));
  return {
    snapshot: { queryParamMap: subject.value },
    queryParamMap: subject.asObservable() as BehaviorSubject<ParamMap>,
  };
}

// ── Suite ───────────────────────────────────────────────────

describe('SnapshotsComponent (v2 redesign)', () => {
  let fixture: ComponentFixture<SnapshotsComponent>;
  let component: SnapshotsComponent;

  let mockSnapshotService: {
    list: jest.Mock;
    getById: jest.Mock;
    getMetrics: jest.Mock;
  };
  let mockSettingsService: {
    getSnapshotCreateEnabled: jest.Mock;
    setSnapshotCreateEnabled: jest.Mock;
    getSnapshotUsageMetrics: jest.Mock;
  };
  let mockProjectService: {
    projects: ReturnType<typeof signal<Project[]>>;
    listProjects: jest.Mock;
  };
  let clipboard: { copy: jest.Mock };
  let snackBar: { open: jest.Mock };
  let router: { navigate: jest.Mock };
  let routeStub: ReturnType<typeof makeActivatedRouteStub>;

  beforeEach(async () => {
    mockSnapshotService = {
      list: jest.fn().mockReturnValue(of(makeListResponse())),
      getById: jest.fn(),
      getMetrics: jest.fn().mockReturnValue(of(makeMetrics())),
    };
    mockSettingsService = {
      getSnapshotCreateEnabled: jest.fn().mockReturnValue(of({ enabled: false })),
      setSnapshotCreateEnabled: jest.fn().mockReturnValue(of({ enabled: true })),
      getSnapshotUsageMetrics: jest.fn().mockReturnValue(of(makeMetrics())),
    };
    mockProjectService = {
      projects: signal<Project[]>([]),
      listProjects: jest.fn().mockReturnValue(of({ projects: [], total: 0 })),
    };
    clipboard = { copy: jest.fn() };
    snackBar = { open: jest.fn() };
    router = { navigate: jest.fn().mockResolvedValue(true) };
    routeStub = makeActivatedRouteStub({});

    await TestBed.configureTestingModule({
      imports: [SnapshotsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: SnapshotService, useValue: mockSnapshotService },
        { provide: SettingsService, useValue: mockSettingsService },
        { provide: ProjectService, useValue: mockProjectService },
        { provide: Clipboard, useValue: clipboard },
        { provide: MatSnackBar, useValue: snackBar },
        { provide: Router, useValue: router },
        { provide: ActivatedRoute, useValue: routeStub },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SnapshotsComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // ── (a) v2 chrome renders control row + filter row + stats strip + table
  it('(a) renders control row + filter row + stats strip + table component on init', () => {
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.control-row')).not.toBeNull();
    expect(compiled.querySelector('.filter-row')).not.toBeNull();
    expect(compiled.querySelector('.stats-strip')).not.toBeNull();
    expect(compiled.querySelector('app-snapshots-table')).not.toBeNull();
  });

  // ── (b) 3 parallel fetches fire on ngOnInit (toggle, metrics, list)
  it('(b) fires toggle (GET) + metrics + list on init (3 parallel fetches)', fakeAsync(() => {
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(mockSettingsService.getSnapshotCreateEnabled).toHaveBeenCalled();
    expect(mockSnapshotService.getMetrics).toHaveBeenCalled();
    expect(mockSnapshotService.list).toHaveBeenCalled();
  }));

  // ── (c) Apply in dirty mode calls setSnapshotCreateEnabled with the right payload
  it('(c) clicking Apply when dirty calls setSnapshotCreateEnabled(true)', fakeAsync(() => {
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    component.onSnapshotCreateSelectionChange(true);
    fixture.detectChanges();
    component.saveSnapshotCreateEnabled();
    tick();
    expect(mockSettingsService.setSnapshotCreateEnabled).toHaveBeenCalledWith(true);
  }));

  // ── (d) header-toggle save error → snackbar error, toggle stays dirty
  it('(d) save error shows a snackbar and the toggle stays dirty (saved unchanged)', fakeAsync(() => {
    mockSettingsService.setSnapshotCreateEnabled.mockReturnValue(
      throwError(() => new Error('boom')),
    );
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    component.onSnapshotCreateSelectionChange(true);
    fixture.detectChanges();

    component.saveSnapshotCreateEnabled();
    tick();
    expect(snackBar.open).toHaveBeenCalledWith(
      expect.stringContaining('Failed to save'),
      'Dismiss',
      expect.any(Object),
    );
    expect(component.savedSnapshotCreateEnabled()).toBe(false);
    expect(component.savingSnapshotCreate()).toBe(false);
  }));

  // ── (d-extra) v2 toggle pill click: first click toggles + marks dirty,
  //              second click (when dirty) saves via setSnapshotCreateEnabled.
  it('(d-extra) toggle-pill click: first flips + dirties, second (dirty) saves', fakeAsync(() => {
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.snapshotCreateEnabled()).toBe(false); // initial from mock
    expect(component.snapshotCreateDirty()).toBe(false);

    component.onTogglePillClick(); // first click → flip + dirty
    fixture.detectChanges();
    expect(component.snapshotCreateEnabled()).toBe(true);
    expect(component.snapshotCreateDirty()).toBe(true);

    component.onTogglePillClick(); // second click (dirty) → save
    tick();
    expect(mockSettingsService.setSnapshotCreateEnabled).toHaveBeenCalledWith(true);
  }));

  // ── (e) Clear Filters (filter-clear) visible when hasActiveFilters, hidden when default
  it('(e) Clear Filters is visible when hasActiveFilters and hidden when default', () => {
    fixture.detectChanges();
    let compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('[data-test="filter-clear"]')).toBeNull();

    component.filterAgentId.set('coder');
    fixture.detectChanges();
    compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('[data-test="filter-clear"]')).not.toBeNull();
    expect(compiled.querySelector('[data-test="filter-active-count"]')).not.toBeNull();
  });

  // ── (f) any filter change (incl. Clear-filters) resets pageIndex to 0 IN THE SAME SIGNAL WRITE
  it('(f) any filter change (incl. Clear) resets pageIndex to 0 in the same signal-write', () => {
    component.pageIndex.set(3);
    expect(component.pageIndex()).toBe(3);
    component.onFilterAgentChange('coder');
    expect(component.filterAgentId()).toBe('coder');
    expect(component.pageIndex()).toBe(0);
  });

  it('(f-extra) onClearFilters resets ALL filters + pageIndex to 0 in one call', () => {
    component.pageIndex.set(5);
    component.filterAgentId.set('coder');
    component.filterStatus.set(['active']);
    component.filterAge.set('7d');
    component.onClearFilters();
    expect(component.pageIndex()).toBe(0);
    expect(component.filterAgentId()).toBeNull();
    expect(component.filterStatus()).toEqual([]);
    expect(component.filterAge()).toBe('all');
    expect(component.hasActiveFilters()).toBe(false);
  });

  // ── (g) clicking a row sets selectedSnapshotId() and drawerOpen()=true
  it('(g) row click sets selectedSnapshotId and drawerOpen=true', () => {
    const row = makeRow();
    component.onRowClick(row);
    expect(component.selectedSnapshotId()).toBe('row-uuid-1');
    expect(component.drawerOpen()).toBe(true);
  });

  // ── (h) closing the drawer clears selectedSnapshotId
  it('(h) onCloseDrawer clears selectedSnapshotId and closes the drawer', () => {
    component.onRowClick(makeRow());
    expect(component.selectedSnapshotId()).toBe('row-uuid-1');
    component.onCloseDrawer();
    expect(component.selectedSnapshotId()).toBeNull();
    expect(component.drawerOpen()).toBe(false);
  });

  // ── (i) metrics fetch error → page still usable
  it('(i) metrics fetch error surfaces an error; the page is still usable', fakeAsync(() => {
    mockSnapshotService.getMetrics.mockReturnValue(
      throwError(() => new Error('metrics boom')),
    );
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.metricsError()).toContain('metrics boom');
    expect(component.listError()).toBeNull();
  }));

  // ── (k) R10 cold start — projects() empty → listProjects() called exactly once
  it('(k) cold start with empty projects() calls listProjects() exactly once', fakeAsync(() => {
    expect(mockProjectService.projects().length).toBe(0);
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(mockProjectService.listProjects).toHaveBeenCalledTimes(1);
  }));

  it('(k-extra) warm start with non-empty projects() does NOT call listProjects()', fakeAsync(() => {
    mockProjectService.projects.set([makeProject()]);
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(mockProjectService.listProjects).not.toHaveBeenCalled();
  }));

  // ── (l) agent-filter population — seenAgents reflects distinct agents on every list response
  it('(l) agent-filter population: seenAgents accumulates distinct agents across pages', fakeAsync(() => {
    mockSnapshotService.list
      .mockReturnValueOnce(of(makeListResponse({ items: [makeRow({ created_by_agent_id: 'coder' })], total: 50 })))
      .mockReturnValueOnce(of(makeListResponse({ items: [makeRow({ id: 'r2', created_by_agent_id: 'tester' })], total: 50 })));

    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.seenAgents().has('coder')).toBe(true);

    component.onPageChange({ pageIndex: 1, pageSize: 25, length: 50 });
    tick();
    fixture.detectChanges();
    expect(component.seenAgents().has('coder')).toBe(true);
    expect(component.seenAgents().has('tester')).toBe(true);

    const opts = component.agentOptions();
    expect(opts.map((o) => o.value)).toContain('coder');
    expect(opts.map((o) => o.value)).toContain('tester');
  }));

  // ── (m) selected-agent pin — agent option stays pinned/valid when it is the currently selected value
  it('(m) selected-agent pin: a selected agent stays in agentOptions even when absent from later responses', fakeAsync(() => {
    component.filterAgentId.set('ghost-agent');
    mockSnapshotService.list.mockReturnValueOnce(of(makeListResponse({ items: [], total: 0 })));
    fixture.detectChanges();
    tick();
    fixture.detectChanges();

    const opts = component.agentOptions();
    expect(opts.map((o) => o.value)).toContain('ghost-agent');
  }));

  it('(m-extra) wire: list() is called with buildParams that carry agent_id (D-2)', fakeAsync(() => {
    component.filterAgentId.set('coder');
    component.onFilterAgentChange('coder');
    tick();
    fixture.detectChanges();
    expect(mockSnapshotService.list).toHaveBeenCalled();
    const lastCallFilters: SnapshotFilters =
      mockSnapshotService.list.mock.calls[mockSnapshotService.list.mock.calls.length - 1][0];
    expect(lastCallFilters.agent_id).toBe('coder');
  }));

  // ── (n) v2 NEW: filter signal write mirrors to queryParams (AC-6.3)
  it('(n) AC-6.3: filter signal write mirrors to queryParams via Router.navigate merge', fakeAsync(() => {
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    TestBed.tick();
    // Capture the navigation calls from ngOnInit seed (these use the
    // default filter state so the queryParams object is null-keyed for
    // every entry — i.e. the effect ran with suppressUrlSync=true).
    const initialCalls = router.navigate.mock.calls.length;
    // Now change a filter and force the effect to re-run.
    component.onFilterProjectChange('proj-uuid-1');
    TestBed.tick();
    const afterChangeCalls = router.navigate.mock.calls.length;
    expect(afterChangeCalls).toBeGreaterThan(initialCalls);
    // The latest call must contain the new project_id and merge mode.
    const lastCall = router.navigate.mock.calls[afterChangeCalls - 1];
    expect(lastCall[0]).toEqual(['snapshots']);
    expect(lastCall[1].queryParamsHandling).toBe('merge');
    expect(lastCall[1].queryParams.project_id).toBe('proj-uuid-1');
  }));

  // ── (n-extra) v2 NEW: URL queryParams seed the filter signals on init (AC-6.3)
  it('(n-extra) AC-6.3: ActivatedRoute.queryParams seed the filter signals', fakeAsync(() => {
    // Reset and re-create with a populated queryParamMap.
    TestBed.resetTestingModule();
    routeStub = makeActivatedRouteStub({
      project_id: 'proj-seed-uuid',
      agent_id: 'seed-agent',
      status: ['active', 'running'],
      age: '7d',
      tag_mode: 'any',
      sort: 'title_asc',
      tags: ['domain:api', 'env:prod'],
    });
    TestBed.configureTestingModule({
      imports: [SnapshotsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: SnapshotService, useValue: mockSnapshotService },
        { provide: SettingsService, useValue: mockSettingsService },
        { provide: ProjectService, useValue: mockProjectService },
        { provide: Clipboard, useValue: clipboard },
        { provide: MatSnackBar, useValue: snackBar },
        { provide: Router, useValue: router },
        { provide: ActivatedRoute, useValue: routeStub },
      ],
    });
    TestBed.compileComponents();
    fixture = TestBed.createComponent(SnapshotsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    tick();
    flushMicrotasks();

    expect(component.filterProjectId()).toBe('proj-seed-uuid');
    expect(component.filterAgentId()).toBe('seed-agent');
    expect(component.filterStatus()).toEqual(['active', 'running']);
    expect(component.filterAge()).toBe('7d');
    expect(component.filterTagMode()).toBe('any');
    expect(component.filterSort()).toBe('title_asc');
    expect(component.filterTags()).toEqual(['domain:api', 'env:prod']);
  }));

  // ── (o) v2 NEW: stats-strip reads from records + metrics
  it('(o) AC-2.3: stats-strip counts reflect in-memory records + metrics', fakeAsync(() => {
    mockSnapshotService.list.mockReturnValue(
      of(
        makeListResponse({
          items: [
            makeRow({ id: 'a', status: 'active' }),
            makeRow({ id: 'b', status: 'active' }),
            makeRow({ id: 'c', status: 'running' }),
            makeRow({ id: 'd', status: 'failed' }),
            makeRow({ id: 'e', status: 'interrupted' }),
          ],
          total: 47,
        }),
      ),
    );
    mockSnapshotService.getMetrics.mockReturnValue(
      of(
        makeMetrics({
          spawn_counts_per_snapshot: [
            { snapshot_id: 'a', count: 3 },
            { snapshot_id: 'b', count: 1 },
          ],
        }),
      ),
    );
    fixture.detectChanges();
    tick();
    fixture.detectChanges();

    expect(component.totalSnapshotCount()).toBe(47);
    expect(component.statusCounts().active).toBe(2);
    expect(component.statusCounts().running).toBe(1);
    expect(component.statusCounts().failed).toBe(1);
    expect(component.statusCounts().interrupted).toBe(1);
    expect(component.totalWarmedSpawns()).toBe(4);
  }));
});

// (of/BehaviorSubject/throwError imports hoisted to the top of the file)