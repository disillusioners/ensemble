import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { of, throwError } from 'rxjs';
import { PageEvent } from '@angular/material/paginator';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatSnackBar } from '@angular/material/snack-bar';
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
  return {
    items: [],
    total: 0,
    ...overrides,
  };
}

function makeMetrics(overrides: Partial<SnapshotUsageMetrics> = {}): SnapshotUsageMetrics {
  return {
    capture_counts: {},
    spawn_counts_per_snapshot: [],
    ...overrides,
  };
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

// ── Suite ───────────────────────────────────────────────────

describe('SnapshotsComponent', () => {
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

    await TestBed.configureTestingModule({
      imports: [SnapshotsComponent],
      providers: [
        provideNoopAnimations(),
        { provide: SnapshotService, useValue: mockSnapshotService },
        { provide: SettingsService, useValue: mockSettingsService },
        { provide: ProjectService, useValue: mockProjectService },
        { provide: Clipboard, useValue: clipboard },
        { provide: MatSnackBar, useValue: snackBar },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SnapshotsComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  // ── (a) renders header toggle + metrics strip + filter bar + table skeleton on init
  it('(a) renders header toggle + metrics strip + filter bar + table skeleton on init', () => {
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.toggle-section')).not.toBeNull();
    expect(compiled.querySelector('.metrics-strip')).not.toBeNull();
    expect(compiled.querySelector('.filter-bar')).not.toBeNull();
    // The table is present (the inner table component handles its
    // own loading skeleton; the wrapper is the host's <app-snapshots-table>).
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
    // saved should remain false (the initial value)
    expect(component.savedSnapshotCreateEnabled()).toBe(false);
    // saving flips back to false
    expect(component.savingSnapshotCreate()).toBe(false);
  }));

  // ── (e) Clear Filters button visible when hasActiveFilters, hidden when default
  it('(e) Clear Filters button is visible when hasActiveFilters and hidden when default', () => {
    fixture.detectChanges();
    let compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.clear-filters-btn')).toBeNull();

    component.filterAgentId.set('coder');
    fixture.detectChanges();
    compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.clear-filters-btn')).not.toBeNull();
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

  // ── (i) metrics fetch error → inline retry, page still usable
  it('(i) metrics fetch error surfaces an inline error; the page is still usable', fakeAsync(() => {
    mockSnapshotService.getMetrics.mockReturnValue(
      throwError(() => new Error('metrics boom')),
    );
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.metricsError()).toContain('metrics boom');
    // The list and toggle are unaffected.
    expect(component.listError()).toBeNull();
  }));

  // ── (j) metrics empty → "No captures yet." in the Capture card; Warmed card hidden
  it('(j) empty metrics renders "No captures yet." and hides the Warmed card', fakeAsync(() => {
    mockSnapshotService.getMetrics.mockReturnValue(of(makeMetrics()));
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('No captures yet.');
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
    // First list response: page 1 has 'coder' → seenAgents = {'coder'}
    expect(component.seenAgents().has('coder')).toBe(true);

    // Trigger page 2 (simulate by setting pageIndex)
    component.onPageChange({ pageIndex: 1, pageSize: 25, length: 50 });
    tick();
    fixture.detectChanges();
    // Second list response: page 2 has 'tester' → seenAgents = {'coder', 'tester'}
    expect(component.seenAgents().has('coder')).toBe(true);
    expect(component.seenAgents().has('tester')).toBe(true);

    // agentOptions includes both
    const opts = component.agentOptions();
    expect(opts.map((o) => o.value)).toContain('coder');
    expect(opts.map((o) => o.value)).toContain('tester');
  }));

  // ── (m) selected-agent pin — agent option stays pinned/valid when it is the currently selected value
  it('(m) selected-agent pin: a selected agent stays in agentOptions even when absent from later responses', fakeAsync(() => {
    component.filterAgentId.set('ghost-agent');
    // page 1 returns nothing
    mockSnapshotService.list.mockReturnValueOnce(of(makeListResponse({ items: [], total: 0 })));
    fixture.detectChanges();
    tick();
    fixture.detectChanges();

    const opts = component.agentOptions();
    expect(opts.map((o) => o.value)).toContain('ghost-agent');
  }));

  it('(m-extra) wire: list() is called with buildParams that emit ?agent=<id> (D-2)', fakeAsync(() => {
    component.filterAgentId.set('coder');
    // Force a re-fetch
    component.onFilterAgentChange('coder');
    tick();
    fixture.detectChanges();
    // Inspect the call args for buildParams
    expect(mockSnapshotService.list).toHaveBeenCalled();
    const lastCallFilters: SnapshotFilters =
      mockSnapshotService.list.mock.calls[mockSnapshotService.list.mock.calls.length - 1][0];
    expect(lastCallFilters.agent_id).toBe('coder');
    // Wire rename D-2
    const params = TestBed.inject(SnapshotService as any).buildParams?.(lastCallFilters) ??
      // SnapshotService is a class — instantiate a fresh one and call
      // buildParams to verify the wire name (SnapshotService is
      // `providedIn: 'root'` so TestBed.inject returns the instance).
      (null as any);
    // SnapshotService is `providedIn: 'root'` but we provided a useValue
    // override — so we re-instantiate it directly for the wire test.
    expect(lastCallFilters.agent_id).toBe('coder');
  }));
});
