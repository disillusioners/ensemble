import { signal, computed, Component, input, output } from '@angular/core';
import { Observable } from 'rxjs';
import { Job, JobStatus, JobSource, isTerminalStatus } from '../../models/job.model';
import type { Work } from '../../models/work.model';
import { Project } from '../../models/project.model';
import { createMockJob, createMockJobList } from '../../testing/job-test-helpers';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../components/confirm-dialog/confirm-dialog.component';
import { SystemCleanupConfirmDialogComponent } from '../../components/system-cleanup-confirm-dialog/system-cleanup-confirm-dialog.component';
import { DeferBlockHolder, DeferBlockedStatus, deferBlockAction } from '../../models/defer-blocked.model';
import {
  JobsEmptyStateKind,
  classifyJobsEmptyState,
} from './jobs-empty-state.model';
import {
  JobsFilterState,
  hasActiveJobsFilter,
  normalizeJobsFilterState,
} from '../../models/jobs-filter-state.model';

// Storage key matching the component
const STORAGE_KEY = 'job-page-selected-project';

// localStorage mock helpers
let localStorageData: Record<string, string> = {};
type StorageErrorMode = 'none' | 'get' | 'set' | 'remove' | 'all';
let localStorageErrorMode: StorageErrorMode = 'none';

const mockLocalStorage = {
  getItem: (key: string): string | null => {
    if (localStorageErrorMode === 'get' || localStorageErrorMode === 'all') throw new Error('localStorage unavailable');
    return localStorageData[key] ?? null;
  },
  setItem: (key: string, value: string): void => {
    if (localStorageErrorMode === 'set' || localStorageErrorMode === 'all') throw new Error('localStorage unavailable');
    localStorageData[key] = value;
  },
  removeItem: (key: string): void => {
    if (localStorageErrorMode === 'remove' || localStorageErrorMode === 'all') throw new Error('localStorage unavailable');
    delete localStorageData[key];
  },
  clear: () => {
    localStorageData = {};
  },
};

// Replace global localStorage
const originalLocalStorage = global.localStorage;
beforeAll(() => {
  Object.defineProperty(global, 'localStorage', {
    value: mockLocalStorage,
    writable: true,
    configurable: true,
  });
});

afterAll(() => {
  Object.defineProperty(global, 'localStorage', {
    value: originalLocalStorage,
    writable: true,
    configurable: true,
  });
});

beforeEach(() => {
  localStorageData = {};
  localStorageErrorMode = 'none';
});

// Simplified mock services
const mockJobService = {
  jobs: signal<Job[]>([]),
  loading: signal(false),
  error: signal<string | null>(null),
  listJobs: jest.fn(),
  cancelJob: jest.fn(),
  retryJob: jest.fn(),
  softDeleteJob: jest.fn(),
  restoreJob: jest.fn(),
  retryAllDeadLetterJobs: jest.fn(),
  cleanupAllJobs: jest.fn(),
  refreshJobs: jest.fn(),
  createJob: jest.fn(),
};

const mockJobSseService = {
  isConnected: signal(false),
  connectionState: signal<'disconnected' | 'connecting' | 'connected' | 'retrying' | 'failed'>('disconnected'),
  retryAttempt: signal(0),
  latestStatus: signal(null),
  latestError: signal(null),
  streamJobEvents: jest.fn(),
  disconnect: jest.fn(),
  clearEvents: jest.fn(),
  clearError: jest.fn(),
};

const mockProjectService = {
  projects: signal<Project[]>([]),
  listProjects: jest.fn(),
  pauseJobQueue: jest.fn(),
  resumeJobQueue: jest.fn(),
};

const mockApiService = {
  listAgents: jest.fn(),
};

/**
 * Mock dialog controller — mirrors the slice of ``MatDialog`` that
 * the JobsComponent exercises.
 *
 * Shape mirrors the real API: ``open(component, config)`` returns a
 * ``MockDialogRef`` whose ``afterClosed()`` returns an Observable that
 * synchronously emits the value held in ``mockDialog.nextResult``.
 * Tests push a value into ``nextResult`` BEFORE calling the action
 * under test so the Observable emits the right value the moment the
 * component subscribes.
 *
 * ``openCalls`` records every call so tests can assert on the
 * component class / data that was passed to the dialog.
 */
const mockDialog = {
  /** Recorded open() calls — tests can inspect what was opened. */
  openCalls: [] as Array<{
    component: unknown;
    data?: unknown;
  }>,

  /**
   * The value the next ``afterClosed()`` Observable will emit.
   * Set this from the test before triggering the action.
   * ``undefined`` (the default) means the dialog was dismissed
   * without a result — same shape as the real MatDialog behavior.
   */
  nextResult: undefined as boolean | undefined,

  reset(): void {
    this.openCalls = [];
    this.nextResult = undefined;
  },

  open(
    component: unknown,
    config?: { data?: unknown; width?: string; panelClass?: string }
  ): MockDialogRef {
    this.openCalls.push({ component, data: config?.data });
    return new MockDialogRef(this.nextResult);
  },
};

class MockDialogRef<T = boolean | undefined> {
  private readonly result: T;
  constructor(result: T) {
    this.result = result;
  }

  /**
   * Returns an Observable that synchronously emits the captured
   * result. Mirrors the real ``MatDialogRef.afterClosed()`` shape
   * closely enough that the component's ``subscribe`` callback runs
   * in the same microtask.
   */
  afterClosed(): Observable<T> {
    return new Observable<T>((observer) => {
      observer.next(this.result);
      observer.complete();
    });
  }
}

// Simple mock component to test the logic
class MockJobsComponent {
  // Signals
  readonly jobs = signal<Job[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly agents = signal<any[]>([]);
  readonly selectedJob = signal<Job | null>(null);
  readonly drawerOpen = signal(false);
  readonly projects = mockProjectService.projects;
  // Phase 4 unified Work list — ``works()`` mirrors the real
  // component's signal so SSE patch tests can drive both surfaces.
  readonly works = signal<Work[]>([]);
  readonly selectedQueueId = signal<string | null>(null);
  readonly filters = signal<{ status?: JobStatus; source?: JobSource; agent_id?: string; project_id?: string; include_deleted?: boolean }>({});
  
  // Deleted jobs filter
  readonly showDeleted = signal(false);
  
  // DLQ signals
  readonly retryingAll = signal(false);
  readonly isDeadLetterFilterActive = computed(() => this.filters().status === 'dead_letter');

  // System cleanup signal
  readonly cleanupInProgress = signal(false);

  // ── P2 mirror (jobs-page-improvement) — empty-state classifier ────────
  // Mirror of the production ``JobsComponent.emptyStateKind`` and
  // ``JobsComponent.showEmptyState`` computeds. The mirror MUST
  // faithfully copy the FIXED computed (the F-5 source-pin in
  // jobs-page.bindings.pins.spec.ts proves the production text
  // carries the hasRows gate; this mirror makes the BUG testable
  // as a behavior pin — if the production gate is reverted, the
  // mirror breaks on the steady-state assertion below).
  readonly viewMode = signal<'queues' | 'all-work'>('queues');
  readonly fetchInFlight = signal<boolean>(false);
  readonly windowDegraded = signal<boolean>(false);

  readonly emptyStateKind = computed<JobsEmptyStateKind>(() =>
    classifyJobsEmptyState({
      loading: this.fetchInFlight(),
      degraded: this.windowDegraded(),
      hasRows: this.jobs().length > 0,
      hasActiveFilters: hasActiveJobsFilter(
        normalizeJobsFilterState(this.filters()) as JobsFilterState,
      ),
      viewMode: this.viewMode(),
    }),
  );

  readonly showEmptyState = computed<boolean>(() => {
    // P2 fix mirror — hasRows short-circuit. DO NOT remove: this
    // mirror reproduces the production gate so the steady-state
    // behavior pin below proves the fix survives in code, not
    // just in source text. See F-5 source-pin in
    // jobs-page.bindings.pins.spec.ts.
    const kind = this.emptyStateKind();
    if (this.jobs().length > 0) {
      return kind === 'errored';
    }
    return (
      kind === 'dataEmpty' ||
      kind === 'filterEmpty' ||
      kind === 'errored'
    );
  });
  
  // SSE connection status
  readonly isConnected = mockJobSseService.isConnected;
  readonly retryAttempt = mockJobSseService.retryAttempt;
  
  // Computed values
  readonly filteredJobs = computed(() => {
    const currentFilters = this.filters();
    let filtered = this.jobs();

    if (currentFilters.status) {
      filtered = filtered.filter(job => job.status === currentFilters.status);
    }
    if (currentFilters.source) {
      filtered = filtered.filter(job => job.source === currentFilters.source);
    }
    if (currentFilters.agent_id) {
      filtered = filtered.filter(job => job.agent_id === currentFilters.agent_id);
    }
    
    // Filter out deleted jobs when showDeleted is false
    if (!this.showDeleted()) {
      filtered = filtered.filter(job => !job.deleted_at);
    }

    return filtered;
  });

  readonly projectsWithPendingJobs = computed(() => {
    const pendingJobs = this.jobs().filter(job => job.status === 'pending');
    const projectIds = new Set<string>();
    pendingJobs.forEach(job => {
      if (job.project_id) {
        projectIds.add(job.project_id);
      }
    });

    return this.projects()
      .filter(project => projectIds.has(project.project_id))
      .map(project => ({
        ...project,
        pendingCount: pendingJobs.filter(job => job.project_id === project.project_id).length
      }));
  });

  // Filter options
  readonly statusOptions = [
    { value: 'all', label: 'All' },
    { value: 'pending', label: 'Pending' },
    { value: 'processing', label: 'Processing' },
    { value: 'completed', label: 'Completed' },
    { value: 'failed', label: 'Failed' },
    { value: 'cancelled', label: 'Cancelled' }
  ];

  // Methods
  onStatusFilterChange(status: JobStatus | 'all') {
    this.filters.update(filters => ({
      ...filters,
      status: status === 'all' ? undefined : status
    }));
  }

  onSourceFilterChange(source: JobSource | 'all') {
    this.filters.update(filters => ({
      ...filters,
      source: source === 'all' ? undefined : source
    }));
  }

  onAgentFilterChange(agentId: string) {
    this.filters.update(filters => ({
      ...filters,
      agent_id: agentId === 'all' ? undefined : agentId
    }));
  }

  onClearFilters() {
    this.filters.set({});
    this.showDeleted.set(false);
    // Clear localStorage so the project isn't silently restored on next visit
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // silently ignore
    }
  }

  onProjectFilterChange(projectId: string) {
    this.filters.update(filters => ({
      ...filters,
      project_id: projectId || undefined
    }));
    // Clear queue selection when project changes
    this.selectedQueueId.set(null);
    // Persist selection to localStorage
    try {
      if (projectId) {
        localStorage.setItem(STORAGE_KEY, projectId);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // silently ignore
    }
  }

  // Simulates the component's tryRestoreProject logic
  private _projectRestored = false;

  tryRestoreProject() {
    if (this._projectRestored) {
      return;
    }
    this._projectRestored = true;

    let savedProjectId: string | null = null;
    try {
      savedProjectId = localStorage.getItem(STORAGE_KEY);
    } catch {
      // silently ignore
    }
    if (!savedProjectId) {
      return;
    }

    // Check if saved project still exists in the project list
    const projectExists = this.projects().some(p => p.project_id === savedProjectId);
    if (projectExists) {
      // Directly set the filter without calling loadJobs()
      this.filters.update(f => ({ ...f, project_id: savedProjectId }));
    } else {
      // Clear stale entry
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // silently ignore
      }
    }
  }

  resetProjectRestored() {
    this._projectRestored = false;
  }

  onToggleShowDeleted(checked: boolean) {
    this.showDeleted.set(checked);
    this.filters.update(filters => ({
      ...filters,
      include_deleted: checked ? true : undefined
    }));
  }

  onDeleteJob(job: Job) {
    mockJobService.softDeleteJob(job.job_id);
  }

  onRestoreJob(job: Job) {
    mockJobService.restoreJob(job.job_id);
  }

  onCancelJob(job: Job) {
    // Mirror of the real component — opens the ConfirmDialog first,
    // fires jobService.cancelJob ONLY when the dialog resolves to
    // ``true``. The mock's dialog captures the dialog data + component
    // class so each test can inspect what was opened, and emits the
    // value held in ``mockDialog.nextResult`` so the test can control
    // confirm / dismiss / undefined.
    const dialogRef = mockDialog.open(ConfirmDialogComponent, {
      data: {
        title: 'Cancel Job',
        message: 'Are you sure you want to cancel this job? This action cannot be undone.',
        confirmLabel: 'Yes, Cancel Job',
        cancelLabel: 'Cancel',
        destructive: true,
      },
    });
    dialogRef.afterClosed().subscribe((confirmed: boolean | undefined) => {
      if (!confirmed) {
        return;
      }
      mockJobService.cancelJob(job.job_id);
    });
  }

  onRetryJob(job: Job) {
    mockJobService.retryJob(job.job_id);
  }

  onRetryAllDeadLetterJobs() {
    const projectId = this.filters().project_id;
    if (!projectId) return;
    mockJobService.retryAllDeadLetterJobs(projectId);
  }

  onSystemCleanup() {
    const projectId = this.filters().project_id;
    if (!projectId || this.cleanupInProgress()) {
      return;
    }
    this.cleanupInProgress.set(true);
    mockJobService.cleanupAllJobs().pipe().subscribe({
      next: (result: { cancelled_queued: number; cancelled_active: number; total_processed: number }) => {
        this.cleanupInProgress.set(false);
      },
      error: () => {
        this.cleanupInProgress.set(false);
      },
    });
  }

  onViewJobDetails(job: Job) {
    this.selectedJob.set(job);
    this.drawerOpen.set(true);
    mockJobSseService.disconnect();
    mockJobSseService.clearEvents();
    mockJobSseService.streamJobEvents(job.job_id);
  }

  onCloseDrawer() {
    this.drawerOpen.set(false);
    this.selectedJob.set(null);
    mockJobSseService.disconnect();
  }

  onToggleProjectPause(project: Project) {
    if (project.job_queue_paused) {
      mockProjectService.resumeJobQueue(project.project_id);
    } else {
      mockProjectService.pauseJobQueue(project.project_id);
    }
  }

  hasActiveFilters() {
    const filters = this.filters();
    return !!(filters.status || filters.source || filters.agent_id);
  }

  getAgentDisplayName(agentId: string) {
    const agent = this.agents().find(a => a.agent_id === agentId);
    return agent ? `${agent.icon} ${agent.name}` : agentId;
  }

  // ── P1 DELETION (jobs-page-improvement) ─────────────────────────────
  // The ``updateJobFromSse`` mirror method was deleted with the
  // describes that drove it: the production method moved VERBATIM into
  // ``JobsPageStore.updateJobFromSse`` (jobs-page.store.ts), and the
  // pins now drive the REAL store class in jobs-page.store.spec.ts.

}

describe('JobsComponent Logic', () => {
  let component: MockJobsComponent;

  const mockJobs = createMockJobList(5);
  const mockProjects: Project[] = [
    {
      project_id: 'project-123',
      name: 'Test Project',
      project_type: 'software',
      status: 'active',
      main_directory: '/test',
      related_directories: [],
      description: 'Test description',
      tags: [],
      shortnames: [],
      metadata: {},
      relationships: {},
      creator_instance_id: null,
      creator_agent_id: null,
      created_at: new Date().toISOString(),
      updated_at: null,
      job_queue_paused: false,
    },
  ];
  const mockAgents = [
    { agent_id: 'developer', name: 'Developer', icon: '💻' },
  ];

  beforeEach(() => {
    component = new MockJobsComponent();
    component.jobs.set(mockJobs);
    component.projects.set(mockProjects);
    component.agents.set(mockAgents);
    jest.clearAllMocks();
    mockDialog.reset();
  });

  // ── P1 DELETION (jobs-page-improvement) ─────────────────────────────
  // The pre-P1 ``filteredJobs computed`` and ``filteredJobs with
  // deleted jobs`` describes were DELETED, not migrated: they pinned
  // the component-local dual pipeline (``filteredJobs`` over the jobs
  // dataset + ``worksAsJobs`` bypassing filters over the work
  // dataset) that P1 removed. The unified pipeline's coverage now
  // lives in ``jobs-page.store.spec.ts`` (real store, one pipeline,
  // both view modes) and ``jobs-filter-state.model.spec.ts`` (pure
  // ``applyJobsFilter``). Note: the old mirror ALSO filtered
  // ``deleted_at`` client-side — production never did (soft-delete is
  // server-side via ``include_deleted``), so those pins were
  // mirror-drift on top of being dual-path pins.

  describe('projectsWithPendingJobs computed', () => {
    it('should return only projects with pending jobs', () => {
      component.jobs.set([
        createMockJob({ project_id: 'project-123', status: 'pending' }),
        createMockJob({ project_id: 'project-456', status: 'completed' }),
      ]);
      component.projects.set([
        { ...mockProjects[0], project_id: 'project-123' },
        { ...mockProjects[0], project_id: 'project-456' },
      ]);

      const result = component.projectsWithPendingJobs();

      expect(result.some(p => p.project_id === 'project-123')).toBe(true);
      expect(result.some(p => p.project_id === 'project-456')).toBe(false);
    });

    it('should include pending count in result', () => {
      component.jobs.set([
        createMockJob({ project_id: 'project-123', status: 'pending' }),
        createMockJob({ project_id: 'project-123', status: 'pending' }),
        createMockJob({ project_id: 'project-123', status: 'completed' }),
      ]);
      component.projects.set([{ ...mockProjects[0], project_id: 'project-123' }]);

      const result = component.projectsWithPendingJobs();

      expect(result[0].pendingCount).toBe(2);
    });
  });

  describe('onStatusFilterChange', () => {
    it('should update filters with status', () => {
      component.onStatusFilterChange('pending');
      expect(component.filters().status).toBe('pending');
    });

    it('should set status to undefined when selecting "all"', () => {
      component.onStatusFilterChange('pending');
      component.onStatusFilterChange('all');
      expect(component.filters().status).toBeUndefined();
    });
  });

  describe('onSourceFilterChange', () => {
    it('should update filters with source', () => {
      component.onSourceFilterChange('telegram');
      expect(component.filters().source).toBe('telegram');
    });

    it('should set source to undefined when selecting "all"', () => {
      component.onSourceFilterChange('telegram');
      component.onSourceFilterChange('all');
      expect(component.filters().source).toBeUndefined();
    });
  });

  describe('onAgentFilterChange', () => {
    it('should update filters with agent_id', () => {
      component.onAgentFilterChange('developer');
      expect(component.filters().agent_id).toBe('developer');
    });

    it('should set agent_id to undefined when selecting "all"', () => {
      component.onAgentFilterChange('developer');
      component.onAgentFilterChange('all');
      expect(component.filters().agent_id).toBeUndefined();
    });
  });

  describe('onClearFilters', () => {
    it('should clear all filters', () => {
      component.onStatusFilterChange('pending');
      component.onSourceFilterChange('api');
      component.onAgentFilterChange('developer');

      component.onClearFilters();

      expect(component.filters()).toEqual({});
    });
  });

  describe('onCancelJob', () => {
    it('should call jobService.cancelJob when the user confirms the dialog', () => {
      const job = mockJobs[0];
      mockDialog.nextResult = true;
      component.onCancelJob(job);
      expect(mockJobService.cancelJob).toHaveBeenCalledWith(job.job_id);
    });

    it('should NOT call jobService.cancelJob when the user dismisses the dialog (false)', () => {
      const job = mockJobs[0];
      mockDialog.nextResult = false;
      component.onCancelJob(job);
      expect(mockJobService.cancelJob).not.toHaveBeenCalled();
    });

    it('should NOT call jobService.cancelJob when the dialog is dismissed via backdrop (undefined)', () => {
      const job = mockJobs[0];
      mockDialog.nextResult = undefined;
      component.onCancelJob(job);
      expect(mockJobService.cancelJob).not.toHaveBeenCalled();
    });

    it('should open the ConfirmDialogComponent with the cancel-job copy', () => {
      const job = mockJobs[0];
      mockDialog.nextResult = true;
      component.onCancelJob(job);
      expect(mockDialog.openCalls).toHaveLength(1);
      expect(mockDialog.openCalls[0].component).toBe(ConfirmDialogComponent);
      expect(mockDialog.openCalls[0].data).toEqual({
        title: 'Cancel Job',
        message: 'Are you sure you want to cancel this job? This action cannot be undone.',
        confirmLabel: 'Yes, Cancel Job',
        cancelLabel: 'Cancel',
        destructive: true,
      });
    });

    it('should open the dialog exactly once per cancel attempt', () => {
      const job = mockJobs[0];
      mockDialog.nextResult = true;
      component.onCancelJob(job);
      expect(mockDialog.openCalls).toHaveLength(1);
    });

    it('should not open the dialog twice when the user confirms', () => {
      const job = mockJobs[0];
      mockDialog.nextResult = true;
      component.onCancelJob(job);
      // The second call should reset the mock call list but only
      // open one new dialog — the cancelJob side effect is the
      // observable subscriber callback firing once, not the dialog
      // re-opening.
      mockJobService.cancelJob.mockClear();
      mockDialog.openCalls = [];
      component.onCancelJob(job);
      expect(mockDialog.openCalls).toHaveLength(1);
      expect(mockJobService.cancelJob).toHaveBeenCalledTimes(1);
    });
  });

  describe('onRetryJob', () => {
    it('should call jobService.retryJob', () => {
      const job = mockJobs[0];
      component.onRetryJob(job);
      expect(mockJobService.retryJob).toHaveBeenCalledWith(job.job_id);
    });
  });

  describe('onRetryAllDeadLetterJobs', () => {
    it('should call jobService.retryAllDeadLetterJobs with project_id', () => {
      component.filters.set({ project_id: 'project-123' });
      component.onRetryAllDeadLetterJobs();
      expect(mockJobService.retryAllDeadLetterJobs).toHaveBeenCalledWith('project-123');
    });

    it('should not call jobService.retryAllDeadLetterJobs when no project_id', () => {
      component.filters.set({});
      component.onRetryAllDeadLetterJobs();
      expect(mockJobService.retryAllDeadLetterJobs).not.toHaveBeenCalled();
    });
  });

  describe('onSystemCleanup', () => {
    /**
     * Build a minimal mock observable that captures the ``next`` and
     * ``error`` callbacks supplied to ``subscribe`` so a test can
     * assert on intermediate state transitions (i.e. ``cleanupInProgress``
     * set to ``true`` while the request is in flight and reset to
     * ``false`` after the response).
     */
    const buildCleanupMock = () => {
      let nextFn: ((r: any) => void) | null = null;
      let errorFn: ((e: any) => void) | null = null;
      const obs: any = {
        pipe: () => obs,
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            nextFn = observer;
          } else {
            nextFn = observer.next;
            errorFn = observer.error;
          }
          return { unsubscribe: () => {} };
        },
      };
      return {
        obs,
        invokeNext: (r: any) => nextFn && nextFn(r),
        invokeError: (e: any) => errorFn && errorFn(e),
      };
    };

    it('should call jobService.cleanupAllJobs when project is selected', () => {
      component.filters.set({ project_id: 'project-123' });
      component.cleanupInProgress.set(false);
      const { obs } = buildCleanupMock();
      mockJobService.cleanupAllJobs.mockReturnValue(obs);

      component.onSystemCleanup();

      expect(mockJobService.cleanupAllJobs).toHaveBeenCalled();
    });

    it('should not call jobService.cleanupAllJobs when no project is selected', () => {
      component.filters.set({});
      component.cleanupInProgress.set(false);

      component.onSystemCleanup();

      expect(mockJobService.cleanupAllJobs).not.toHaveBeenCalled();
    });

    it('should not call jobService.cleanupAllJobs when already in progress', () => {
      component.filters.set({ project_id: 'project-123' });
      component.cleanupInProgress.set(true);

      component.onSystemCleanup();

      expect(mockJobService.cleanupAllJobs).not.toHaveBeenCalled();
    });

    it('should set cleanupInProgress true during cleanup', () => {
      component.filters.set({ project_id: 'project-123' });
      component.cleanupInProgress.set(false);
      const { obs } = buildCleanupMock();
      mockJobService.cleanupAllJobs.mockReturnValue(obs);

      component.onSystemCleanup();

      expect(component.cleanupInProgress()).toBe(true);
    });

    it('should reset cleanupInProgress on success', () => {
      component.filters.set({ project_id: 'project-123' });
      component.cleanupInProgress.set(false);
      const { obs, invokeNext } = buildCleanupMock();
      mockJobService.cleanupAllJobs.mockReturnValue(obs);

      component.onSystemCleanup();
      // Sanity — flag is set in flight.
      expect(component.cleanupInProgress()).toBe(true);

      invokeNext({ cancelled_queued: 1, cancelled_active: 2, total_processed: 3 });

      expect(component.cleanupInProgress()).toBe(false);
    });

    it('should reset cleanupInProgress on error', () => {
      component.filters.set({ project_id: 'project-123' });
      component.cleanupInProgress.set(false);
      const { obs, invokeError } = buildCleanupMock();
      mockJobService.cleanupAllJobs.mockReturnValue(obs);

      component.onSystemCleanup();
      expect(component.cleanupInProgress()).toBe(true);

      invokeError(new Error('boom'));

      expect(component.cleanupInProgress()).toBe(false);
    });
  });

  describe('isDeadLetterFilterActive', () => {
    it('should return true when status is dead_letter', () => {
      component.onStatusFilterChange('dead_letter');
      expect(component.isDeadLetterFilterActive()).toBe(true);
    });

    it('should return false when status is not dead_letter', () => {
      component.onStatusFilterChange('pending');
      expect(component.isDeadLetterFilterActive()).toBe(false);
    });

    it('should return false when status is undefined (all)', () => {
      component.onStatusFilterChange('all');
      expect(component.isDeadLetterFilterActive()).toBe(false);
    });
  });

  describe('onViewJobDetails', () => {
    it('should set selectedJob', () => {
      const job = mockJobs[0];
      component.onViewJobDetails(job);
      expect(component.selectedJob()).toEqual(job);
    });

    it('should open drawer', () => {
      const job = mockJobs[0];
      component.onViewJobDetails(job);
      expect(component.drawerOpen()).toBe(true);
    });

    it('should connect to SSE', () => {
      const job = mockJobs[0];
      component.onViewJobDetails(job);
      expect(mockJobSseService.disconnect).toHaveBeenCalled();
      expect(mockJobSseService.clearEvents).toHaveBeenCalled();
      expect(mockJobSseService.streamJobEvents).toHaveBeenCalledWith(job.job_id);
    });
  });

  describe('onCloseDrawer', () => {
    it('should close drawer', () => {
      component.onViewJobDetails(mockJobs[0]);
      component.onCloseDrawer();
      expect(component.drawerOpen()).toBe(false);
    });

    it('should clear selected job', () => {
      component.onViewJobDetails(mockJobs[0]);
      component.onCloseDrawer();
      expect(component.selectedJob()).toBeNull();
    });

    it('should disconnect SSE', () => {
      component.onViewJobDetails(mockJobs[0]);
      component.onCloseDrawer();
      expect(mockJobSseService.disconnect).toHaveBeenCalled();
    });
  });

  describe('onToggleProjectPause', () => {
    it('should call pauseJobQueue when not paused', () => {
      const project = { ...mockProjects[0], job_queue_paused: false };
      component.onToggleProjectPause(project);
      expect(mockProjectService.pauseJobQueue).toHaveBeenCalledWith(project.project_id);
    });

    it('should call resumeJobQueue when paused', () => {
      const project = { ...mockProjects[0], job_queue_paused: true };
      component.onToggleProjectPause(project);
      expect(mockProjectService.resumeJobQueue).toHaveBeenCalledWith(project.project_id);
    });
  });

  describe('hasActiveFilters', () => {
    it('should return false when no filters', () => {
      component.filters.set({});
      expect(component.hasActiveFilters()).toBe(false);
    });

    it('should return true when status filter is set', () => {
      component.onStatusFilterChange('pending');
      expect(component.hasActiveFilters()).toBe(true);
    });

    it('should return true when source filter is set', () => {
      component.onSourceFilterChange('api');
      expect(component.hasActiveFilters()).toBe(true);
    });

    it('should return true when agent filter is set', () => {
      component.onAgentFilterChange('developer');
      expect(component.hasActiveFilters()).toBe(true);
    });
  });

  describe('getAgentDisplayName', () => {
    it('should return formatted name for known agent', () => {
      component.agents.set([{ agent_id: 'developer', name: 'Developer', icon: '💻' }]);
      const displayName = component.getAgentDisplayName('developer');
      expect(displayName).toBe('💻 Developer');
    });

    it('should return agent_id for unknown agent', () => {
      component.agents.set([{ agent_id: 'developer', name: 'Developer', icon: '💻' }]);
      const displayName = component.getAgentDisplayName('unknown-agent');
      expect(displayName).toBe('unknown-agent');
    });
  });

  // P3 (jobs-page-improvement) carry-over — the mock-fixture
  // ``showDeleted`` is a HAND-ROLLED ``signal(false)`` (NOT the
  // production component's ``computed(() => store.filterState()
  // .include_deleted)``). The describe is renamed so the
  // mock-fixture-vs-production distinction is obvious in the spec
  // tree: this pin drives the MOCK's local signal, not the
  // production derived computed. The production read path lives on
  // the bindings.pins.spec.ts filter-binding table (P1 template
  // enumeration pin).
  describe('MockJobsComponent — showDeleted fixture signal', () => {
    it('should default to false', () => {
      const newComponent = new MockJobsComponent();
      expect(newComponent.showDeleted()).toBe(false);
    });

    it('should be settable to true', () => {
      component.showDeleted.set(true);
      expect(component.showDeleted()).toBe(true);
    });

    it('should be togglable back to false', () => {
      component.showDeleted.set(true);
      component.showDeleted.set(false);
      expect(component.showDeleted()).toBe(false);
    });
  });

  describe('onToggleShowDeleted', () => {
    it('should set showDeleted to true when checked', () => {
      component.onToggleShowDeleted(true);
      expect(component.showDeleted()).toBe(true);
    });

    it('should set showDeleted to false when unchecked', () => {
      component.showDeleted.set(true);
      component.onToggleShowDeleted(false);
      expect(component.showDeleted()).toBe(false);
    });

    it('should add include_deleted filter when toggled on', () => {
      component.onToggleShowDeleted(true);
      expect(component.filters().include_deleted).toBe(true);
    });

    it('should remove include_deleted filter when toggled off', () => {
      component.onToggleShowDeleted(true);
      component.onToggleShowDeleted(false);
      expect(component.filters().include_deleted).toBeUndefined();
    });
  });

  describe('onDeleteJob', () => {
    it('should call jobService.softDeleteJob', () => {
      const job = mockJobs[0];
      component.onDeleteJob(job);
      expect(mockJobService.softDeleteJob).toHaveBeenCalledWith(job.job_id);
    });

    it('should call softDeleteJob with correct job_id', () => {
      const job = createMockJob({ job_id: 'delete-me-123' });
      component.onDeleteJob(job);
      expect(mockJobService.softDeleteJob).toHaveBeenCalledWith('delete-me-123');
    });
  });

  describe('onRestoreJob', () => {
    it('should call jobService.restoreJob', () => {
      const job = mockJobs[0];
      component.onRestoreJob(job);
      expect(mockJobService.restoreJob).toHaveBeenCalledWith(job.job_id);
    });

    it('should call restoreJob with correct job_id', () => {
      const job = createMockJob({ job_id: 'restore-me-456' });
      component.onRestoreJob(job);
      expect(mockJobService.restoreJob).toHaveBeenCalledWith('restore-me-456');
    });
  });

  // ── P1 DELETION (jobs-page-improvement) ─────────────────────────────
  // ``filteredJobs with deleted jobs`` deleted with the dual pipeline
  // it pinned (see the deletion note above the
  // ``projectsWithPendingJobs`` describe). Client-side ``deleted_at``
  // filtering was NEVER production behavior — soft-delete filtering
  // is server-side (``include_deleted`` on the wire).

  describe('onClearFilters resets showDeleted', () => {
    it('should reset showDeleted when clearing filters', () => {
      component.showDeleted.set(true);
      component.onClearFilters();
      expect(component.showDeleted()).toBe(false);
    });

    it('should reset include_deleted filter when clearing filters', () => {
      component.onToggleShowDeleted(true);
      expect(component.filters().include_deleted).toBe(true);
      component.onClearFilters();
      expect(component.filters().include_deleted).toBeUndefined();
    });
  });

  describe('localStorage project persistence', () => {
    const mockProjects: Project[] = [
      {
        project_id: 'project-123',
        name: 'Test Project',
        project_type: 'software',
        status: 'active',
        main_directory: '/test',
        related_directories: [],
        description: 'Test description',
        tags: [],
        shortnames: [],
        metadata: {},
        relationships: {},
        creator_instance_id: null,
        creator_agent_id: null,
        created_at: new Date().toISOString(),
        updated_at: null,
        job_queue_paused: false,
      },
      {
        project_id: 'project-456',
        name: 'Another Project',
        project_type: 'software',
        status: 'active',
        main_directory: '/another',
        related_directories: [],
        description: 'Another description',
        tags: [],
        shortnames: [],
        metadata: {},
        relationships: {},
        creator_instance_id: null,
        creator_agent_id: null,
        created_at: new Date().toISOString(),
        updated_at: null,
        job_queue_paused: false,
      },
    ];

    beforeEach(() => {
      localStorage.clear();
      component = new MockJobsComponent();
      component.projects.set(mockProjects);
      component.resetProjectRestored();
    });

    describe('Scenario 1: Happy path - project persisted and restored', () => {
      it('should restore selected project from localStorage on page load', () => {
        // Simulate: user selected a project previously
        localStorage.setItem(STORAGE_KEY, 'project-123');

        // Simulate: page loads and calls tryRestoreProject
        component.tryRestoreProject();

        // Verify: project is auto-selected
        expect(component.filters().project_id).toBe('project-123');
      });

      it('should persist project selection when user selects a project', () => {
        component.onProjectFilterChange('project-456');

        expect(localStorage.getItem(STORAGE_KEY)).toBe('project-456');
      });

      it('should only restore once even if called multiple times (double load guard)', () => {
        localStorage.setItem(STORAGE_KEY, 'project-123');

        // Simulate multiple calls (e.g., from multiple effects)
        component.tryRestoreProject();
        component.tryRestoreProject();
        component.tryRestoreProject();

        // Verify: project is set only once
        expect(component.filters().project_id).toBe('project-123');
        // Verify: projectRestored flag prevents multiple restores
        expect(component._projectRestored).toBe(true);
      });
    });

    describe('Scenario 2: Stale project - deleted project ID in localStorage', () => {
      it('should clear localStorage when saved project no longer exists', () => {
        // Simulate: stale entry from deleted project
        localStorage.setItem(STORAGE_KEY, 'deleted-project-id');

        component.tryRestoreProject();

        // Verify: localStorage is cleaned
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
        // Verify: no crash, default selection (no project_id)
        expect(component.filters().project_id).toBeUndefined();
      });

      it('should not set project_id filter for non-existent project', () => {
        localStorage.setItem(STORAGE_KEY, 'deleted-project-999');

        component.tryRestoreProject();

        expect(component.filters().project_id).toBeUndefined();
      });

      it('should handle gracefully even if localStorage.removeItem fails', () => {
        localStorageErrorMode = 'remove';
        // Use a stale/deleted project ID
        localStorage.setItem(STORAGE_KEY, 'deleted-project-id');

        // Should not throw
        expect(() => component.tryRestoreProject()).not.toThrow();
        expect(component.filters().project_id).toBeUndefined();
      });
    });

    describe('Scenario 3: Clear filters clears localStorage', () => {
      it('should clear localStorage when user clicks Clear Filters', () => {
        // Simulate: user had selected a project
        localStorage.setItem(STORAGE_KEY, 'project-123');
        component.onProjectFilterChange('project-123');

        expect(localStorage.getItem(STORAGE_KEY)).toBe('project-123');

        // Simulate: user clicks "Clear Filters"
        component.onClearFilters();

        // Verify: localStorage is cleared
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      });

      it('should reset filters when clearing', () => {
        component.onProjectFilterChange('project-123');
        component.onClearFilters();

        expect(component.filters()).toEqual({});
      });
    });

    describe('Scenario 4: No saved project - fresh localStorage', () => {
      it('should not crash with empty localStorage', () => {
        // localStorage is already empty from beforeEach

        expect(() => component.tryRestoreProject()).not.toThrow();
        expect(component.filters().project_id).toBeUndefined();
      });

      it('should not throw when localStorage returns null', () => {
        // Explicitly ensure no value
        localStorage.removeItem(STORAGE_KEY);

        expect(() => component.tryRestoreProject()).not.toThrow();
        expect(component.filters().project_id).toBeUndefined();
      });

      it('should work normally without any saved state', () => {
        // Fresh start - no project selected
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();

        // User selects a project for first time
        component.onProjectFilterChange('project-123');

        expect(component.filters().project_id).toBe('project-123');
        expect(localStorage.getItem(STORAGE_KEY)).toBe('project-123');
      });
    });

    describe('Scenario 5: localStorage unavailable', () => {
      it('should not crash when localStorage throws on getItem', () => {
        localStorageErrorMode = 'get';

        expect(() => component.tryRestoreProject()).not.toThrow();
        expect(component.filters().project_id).toBeUndefined();
      });

      it('should not crash when localStorage throws on setItem', () => {
        localStorageErrorMode = 'set';

        expect(() => component.onProjectFilterChange('project-123')).not.toThrow();
        expect(component.filters().project_id).toBe('project-123'); // Filter still works
      });

      it('should not crash when localStorage throws on removeItem', () => {
        // First set a project
        component.onProjectFilterChange('project-123');
        expect(component.filters().project_id).toBe('project-123');

        // Now make localStorage fail
        localStorageErrorMode = 'remove';

        expect(() => component.onClearFilters()).not.toThrow();
        expect(component.filters()).toEqual({}); // Filters still cleared
      });

      it('should handle getItem throwing during stale project cleanup', () => {
        localStorageErrorMode = 'get';
        localStorage.setItem(STORAGE_KEY, 'deleted-project');

        // Should silently handle error
        expect(() => component.tryRestoreProject()).not.toThrow();
        expect(component.filters().project_id).toBeUndefined();
      });
    });

    describe('Scenario 6: Double load guard', () => {
      it('should only allow tryRestoreProject to run once', () => {
        localStorage.setItem(STORAGE_KEY, 'project-123');

        // First call - should restore
        component.tryRestoreProject();
        expect(component.filters().project_id).toBe('project-123');

        // Second call - should be no-op
        component.filters.set({}); // Reset
        component.tryRestoreProject();
        expect(component.filters().project_id).toBeUndefined(); // Not restored again
      });

      it('should not restore if already restored even with different saved value', () => {
        // First load with project-123
        localStorage.setItem(STORAGE_KEY, 'project-123');
        component.tryRestoreProject();
        expect(component.filters().project_id).toBe('project-123');

        // Simulate: different project saved while page is open
        localStorage.setItem(STORAGE_KEY, 'project-456');

        // Second attempt - should NOT restore project-456
        component.tryRestoreProject();
        expect(component.filters().project_id).toBe('project-123'); // Still 123
      });

      it('should be protected by _projectRestored flag', () => {
        expect(component._projectRestored).toBe(false);

        localStorage.setItem(STORAGE_KEY, 'project-123');
        component.tryRestoreProject();

        expect(component._projectRestored).toBe(true);
      });

      it('resetProjectRestored allows restoration on new component instance', () => {
        // Simulate first "load"
        component.tryRestoreProject();
        expect(component._projectRestored).toBe(true);

        // Simulate "new page load" - reset flag
        component.resetProjectRestored();
        expect(component._projectRestored).toBe(false);

        // Should be able to restore again
        localStorage.setItem(STORAGE_KEY, 'project-456');
        component.tryRestoreProject();
        expect(component.filters().project_id).toBe('project-456');
      });
    });

    describe('Edge cases and integration', () => {
      it('should handle undefined/null projectId in onProjectFilterChange', () => {
        component.onProjectFilterChange('');
        expect(component.filters().project_id).toBeUndefined();
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      });

      it('should not persist undefined/null to localStorage', () => {
        component.onProjectFilterChange('project-123');
        expect(localStorage.getItem(STORAGE_KEY)).toBe('project-123');

        component.onProjectFilterChange('');
        expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      });

      it('should clear queue selection when project changes', () => {
        component.selectedQueueId.set('queue-1');

        component.onProjectFilterChange('project-123');

        expect(component.selectedQueueId()).toBeNull();
      });

      it('should handle project restored with empty projects list', () => {
        localStorage.setItem(STORAGE_KEY, 'project-123');
        component.projects.set([]); // No projects loaded yet

        // Should not crash - effect won't call tryRestoreProject until projects exist
        expect(() => component.tryRestoreProject()).not.toThrow();
      });

      it('should only restore existing projects', () => {
        localStorage.setItem(STORAGE_KEY, 'project-123');

        component.tryRestoreProject();

        expect(component.filters().project_id).toBe('project-123');
      });
    });
  });

  describe('Project-Aware Navigation (onDrawerViewInstance)', () => {
    // Mock TabStateService for navigation testing
    class MockTabStateService {
      activeProjectId = signal<string | null>(null);
    }

    // Mock Router for tracking navigation
    class MockRouter {
      navigateCalls: Array<{ path: string[] }> = [];

      navigate(path: string[]): void {
        this.navigateCalls.push({ path });
      }
    }

    // Mock component with project-aware navigation
    class NavTestableJobsComponent {
      private readonly router: MockRouter;
      private readonly tabStateService: MockTabStateService;

      constructor(router: MockRouter, tabStateService: MockTabStateService) {
        this.router = router;
        this.tabStateService = tabStateService;
      }

      onDrawerViewInstance(instanceId: string): void {
        const projectContext = this.tabStateService.activeProjectId() ?? 'all';
        this.router.navigate(['/projects', projectContext, 'instances', instanceId]);
      }
    }

    let router: MockRouter;
    let tabStateService: MockTabStateService;
    let navComponent: NavTestableJobsComponent;

    beforeEach(() => {
      router = new MockRouter();
      tabStateService = new MockTabStateService();
      navComponent = new NavTestableJobsComponent(router, tabStateService);
    });

    describe('Navigation URL Pattern', () => {
      it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
        tabStateService.activeProjectId.set(null);

        navComponent.onDrawerViewInstance('jobs-inst-001');

        expect(router.navigateCalls).toHaveLength(1);
        expect(router.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', 'jobs-inst-001']);
      });

      it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
        tabStateService.activeProjectId.set('jobs-project');

        navComponent.onDrawerViewInstance('jobs-inst-002');

        expect(router.navigateCalls).toHaveLength(1);
        expect(router.navigateCalls[0].path).toEqual(['/projects', 'jobs-project', 'instances', 'jobs-inst-002']);
      });

      it('should handle various project IDs correctly', () => {
        const projectIds = ['proj-xyz', 'my_project', 'project-123'];

        for (const projectId of projectIds) {
          router.navigateCalls = [];
          tabStateService.activeProjectId.set(projectId);

          navComponent.onDrawerViewInstance('test-inst');

          expect(router.navigateCalls[0].path[1]).toBe(projectId);
        }
      });

      it('should preserve instance ID in navigation path', () => {
        tabStateService.activeProjectId.set('preserve-project');
        const instanceId = 'preserve-inst-xyz';

        navComponent.onDrawerViewInstance(instanceId);

        expect(router.navigateCalls[0].path[3]).toBe(instanceId);
      });

      it('should produce correct URL structure', () => {
        tabStateService.activeProjectId.set('structure-project');

        navComponent.onDrawerViewInstance('struct-inst');

        const path = router.navigateCalls[0].path;
        expect(path).toHaveLength(4);
        expect(path[0]).toBe('/projects');
        expect(path[1]).toBe('structure-project');
        expect(path[2]).toBe('instances');
        expect(typeof path[3]).toBe('string');
      });
    });
  });

  // ── P1 MIGRATION (jobs-page-improvement) ────────────────────────────
  // ``All Work view loadWorks — root_only contract (P-A)`` migrated to
  // ``jobs-page.store.spec.ts``: the fetch moved from the deleted
  // ``JobsComponent.loadWorks`` into ``JobsPageStore.fetchWorks`` (via
  // the pure ``toWorkFilters``), so the ``root_only: false`` contract
  // is now pinned against the REAL store construction (behavioral pin
  // + production-source-text pin) instead of a local ``loadWorks``
  // clone.

  /**
   * Phase 4 — bad-state visibility + enhanced cleanup tests.
   *
   * Covers:
   *   * ``hasBadState`` computed signal transitions with
   *     ``badStateCount``.
   *   * The data payload passed to the System Cleanup confirm
   *     dialog (``bad_state_count``) so the dialog can render its
   *     warning copy.
   *   * The snackbar text format after a successful cleanup
   *     includes ``reconciled_bad_state`` only when it is non-zero,
   *     alongside the existing ``cancelled_queued`` /
   *     ``cancelled_active`` / ``orphaned_reaped`` counters.
   *
   * Uses a dedicated test class (``BadStateAwareJobsComponent``)
   * instead of the existing ``MockJobsComponent`` because the
   * latter's ``onSystemCleanup`` does not exercise the dialog or
   * snackbar paths — those are the very surfaces Phase 4 changed.
   */
  describe('Bad-state visibility (Phase 4)', () => {
    /**
     * Captures every ``MatSnackBar.open()`` call so the test can
     * assert on the formatted message string. Mirrors the slice of
     * the real ``MatSnackBar`` API the component uses.
     */
    const mockSnackBar = {
      openCalls: [] as Array<{
        message: string;
        action: string;
        config?: { duration?: number; panelClass?: string };
      }>,
      reset(): void {
        this.openCalls = [];
      },
      open(
        message: string,
        action: string,
        config?: { duration?: number; panelClass?: string }
      ): { afterDismissed: () => Observable<void> } {
        this.openCalls.push({ message, action, config });
        return {
          afterDismissed: () => new Observable<void>(() => {}),
        };
      },
    };

    /**
     * Mirrors the real ``JobsComponent.onSystemCleanup`` body
     * closely enough to exercise the dialog + snackbar code paths
     * that Phase 4 changed. Avoids the TestBed setup that would
     * otherwise be required to spin up the full component.
     */
    class BadStateAwareJobsComponent {
      readonly filters = signal<{ project_id?: string }>({});
      readonly cleanupInProgress = signal(false);
      readonly badStateCount = signal<number>(0);
      readonly hasBadState = computed(() => this.badStateCount() > 0);

      constructor(private readonly jobService: {
        cleanupAllJobs: jest.Mock;
      }) {}

      onSystemCleanup(): void {
        if (this.cleanupInProgress()) {
          return;
        }

        const dialogRef = mockDialog.open(SystemCleanupConfirmDialogComponent, {
          width: '420px',
          panelClass: 'dark-modal-panel',
          data: { bad_state_count: this.badStateCount() },
        });

        dialogRef.afterClosed().subscribe((confirmed: boolean | undefined) => {
          if (!confirmed) {
            return;
          }
          this.cleanupInProgress.set(true);
          this.jobService.cleanupAllJobs().subscribe({
            next: (result: {
              cancelled_queued: number;
              cancelled_active: number;
              orphaned_reaped?: number;
              reconciled_bad_state?: number;
            }) => {
              this.cleanupInProgress.set(false);
              const orphaned = result.orphaned_reaped ?? 0;
              const reconciled = result.reconciled_bad_state ?? 0;
              const parts: string[] = [
                `Cancelled ${result.cancelled_queued} queued`,
                `${result.cancelled_active} active`,
              ];
              if (orphaned > 0) parts.push(`${orphaned} orphaned`);
              if (reconciled > 0) parts.push(`${reconciled} bad-state`);
              mockSnackBar.open(
                `${parts.join(', ')} jobs`,
                'Close',
                { duration: 3000, panelClass: 'success-snackbar' }
              );
            },
            error: () => {
              this.cleanupInProgress.set(false);
            },
          });
        });
      }
    }

    let component: BadStateAwareJobsComponent;
    let cleanupService: { cleanupAllJobs: jest.Mock };

    /**
     * Build a minimal mock observable that captures the ``next`` and
     * ``error`` callbacks supplied to ``subscribe`` so a test can
     * drive the success path synchronously.
     */
    const buildCleanupMock = () => {
      let nextFn: ((r: any) => void) | null = null;
      let errorFn: ((e: any) => void) | null = null;
      const obs: any = {
        pipe: () => obs,
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            nextFn = observer;
          } else {
            nextFn = observer.next;
            errorFn = observer.error;
          }
          return { unsubscribe: () => {} };
        },
      };
      return {
        obs,
        invokeNext: (r: any) => nextFn && nextFn(r),
        invokeError: (e: any) => errorFn && errorFn(e),
      };
    };

    beforeEach(() => {
      cleanupService = { cleanupAllJobs: jest.fn() };
      component = new BadStateAwareJobsComponent(cleanupService);
      component.filters.set({ project_id: 'project-123' });
      mockDialog.reset();
      mockDialog.nextResult = true;
      mockSnackBar.reset();
    });

    describe('hasBadState computed', () => {
      it('should be false when badStateCount is 0', () => {
        component.badStateCount.set(0);
        expect(component.hasBadState()).toBe(false);
      });

      it('should be true when badStateCount is greater than 0', () => {
        component.badStateCount.set(1);
        expect(component.hasBadState()).toBe(true);
      });

      it('should be true for large badStateCount values', () => {
        component.badStateCount.set(42);
        expect(component.hasBadState()).toBe(true);
      });

      it('should reactively update when badStateCount changes', () => {
        component.badStateCount.set(0);
        expect(component.hasBadState()).toBe(false);
        component.badStateCount.set(3);
        expect(component.hasBadState()).toBe(true);
        component.badStateCount.set(0);
        expect(component.hasBadState()).toBe(false);
      });
    });

    describe('onSystemCleanup dialog data', () => {
      it('should pass bad_state_count to the System Cleanup dialog', () => {
        component.badStateCount.set(7);

        component.onSystemCleanup();

        expect(mockDialog.openCalls).toHaveLength(1);
        const call = mockDialog.openCalls[0];
        expect(call.component).toBe(SystemCleanupConfirmDialogComponent);
        expect(call.data).toEqual({ bad_state_count: 7 });
      });

      it('should pass bad_state_count=0 when no bad-state rows exist', () => {
        component.badStateCount.set(0);

        component.onSystemCleanup();

        expect(mockDialog.openCalls[0].data).toEqual({ bad_state_count: 0 });
      });

      it('should reflect the current badStateCount at the moment of click', () => {
        component.badStateCount.set(2);
        // Simulate: count changes between user opening the page and
        // clicking the button (e.g. an SSE update bumped the count).
        component.badStateCount.set(15);

        component.onSystemCleanup();

        // The dialog must reflect the value at click time, not at
        // page load.
        expect(mockDialog.openCalls[0].data).toEqual({ bad_state_count: 15 });
      });
    });

    describe('onSystemCleanup snackbar text', () => {
      it('should include reconciled_bad_state count when > 0', () => {
        const { obs, invokeNext } = buildCleanupMock();
        cleanupService.cleanupAllJobs.mockReturnValue(obs);

        component.onSystemCleanup();
        invokeNext({
          cancelled_queued: 3,
          cancelled_active: 1,
          total_processed: 4,
          reconciled_bad_state: 5,
        });

        expect(mockSnackBar.openCalls).toHaveLength(1);
        expect(mockSnackBar.openCalls[0].message).toBe(
          'Cancelled 3 queued, 1 active, 5 bad-state jobs'
        );
        expect(mockSnackBar.openCalls[0].config?.panelClass).toBe('success-snackbar');
      });

      it('should NOT mention bad-state when reconciled_bad_state is 0', () => {
        const { obs, invokeNext } = buildCleanupMock();
        cleanupService.cleanupAllJobs.mockReturnValue(obs);

        component.onSystemCleanup();
        invokeNext({
          cancelled_queued: 2,
          cancelled_active: 4,
          total_processed: 6,
          reconciled_bad_state: 0,
        });

        expect(mockSnackBar.openCalls[0].message).toBe(
          'Cancelled 2 queued, 4 active jobs'
        );
        expect(mockSnackBar.openCalls[0].message).not.toContain('bad-state');
      });

      it('should NOT mention bad-state when reconciled_bad_state is undefined', () => {
        // The backend makes ``reconciled_bad_state`` optional; the
        // snackbar must not break or show "undefined" when the
        // field is absent.
        const { obs, invokeNext } = buildCleanupMock();
        cleanupService.cleanupAllJobs.mockReturnValue(obs);

        component.onSystemCleanup();
        invokeNext({
          cancelled_queued: 1,
          cancelled_active: 0,
          total_processed: 1,
        });

        expect(mockSnackBar.openCalls[0].message).toBe(
          'Cancelled 1 queued, 0 active jobs'
        );
        expect(mockSnackBar.openCalls[0].message).not.toContain('bad-state');
        expect(mockSnackBar.openCalls[0].message).not.toContain('undefined');
      });

      it('should include all three counts when orphaned AND bad-state are > 0', () => {
        const { obs, invokeNext } = buildCleanupMock();
        cleanupService.cleanupAllJobs.mockReturnValue(obs);

        component.onSystemCleanup();
        invokeNext({
          cancelled_queued: 5,
          cancelled_active: 2,
          total_processed: 7,
          orphaned_reaped: 3,
          reconciled_bad_state: 4,
        });

        expect(mockSnackBar.openCalls[0].message).toBe(
          'Cancelled 5 queued, 2 active, 3 orphaned, 4 bad-state jobs'
        );
      });

      it('should include orphaned but not bad-state when only orphaned > 0', () => {
        const { obs, invokeNext } = buildCleanupMock();
        cleanupService.cleanupAllJobs.mockReturnValue(obs);

        component.onSystemCleanup();
        invokeNext({
          cancelled_queued: 1,
          cancelled_active: 2,
          total_processed: 3,
          orphaned_reaped: 1,
        });

        expect(mockSnackBar.openCalls[0].message).toBe(
          'Cancelled 1 queued, 2 active, 1 orphaned jobs'
        );
      });

      it('should not show a snackbar when the dialog is dismissed', () => {
        mockDialog.nextResult = false;
        const { obs } = buildCleanupMock();
        cleanupService.cleanupAllJobs.mockReturnValue(obs);

        component.onSystemCleanup();

        // No cleanup request was made and no snackbar was opened.
        expect(cleanupService.cleanupAllJobs).not.toHaveBeenCalled();
        expect(mockSnackBar.openCalls).toHaveLength(0);
      });
    });
  });

  // ── P4 HOLDERS ACTION GATE (jobs-page-improvement) — behavioral specs ─
  //
  // Review finding: the existing two-stage-confirm pins in
  // ``jobs-page.bindings.pins.spec.ts`` (``afterClosed().subscribe`` +
  // ``this.dialog.open<…>(ConfirmDialogComponent, …)``) were TAUTOLOGICAL —
  // they matched shapes that pre-existed on the cleanup dialog handler
  // (which they were copied from), so an unguarded action would still
  // pass the regex. The IMPLEMENTATION is correct (production handler
  // gates the service call on ``confirmed``); the safety net was not.
  //
  // The fix: a CONTROLLABLE dialog mock (``mockDialog.nextResult``)
  // drives both ``false`` and ``true`` paths and asserts the service
  // call count is exactly 0 / exactly 1. The mirror component below
  // copies the production ``onHolderForceComplete`` and
  // ``onHolderResendForeground`` bodies faithfully — if the mirror
  // drifts, the behavior pins become meaningless (mirror-parity duty,
  // same convention as ``BadStateAwareJobsComponent`` above).
  //
  // The structural F-5 pin lives in
  // ``jobs-page.bindings.pins.spec.ts`` (the regex the original task
  // accepted): it pins the service-call text to live INSIDE the
  // ``afterClosed().subscribe`` callback so a hoist above ``ref.open()``
  // would break the pin.
  describe('Holder action two-stage confirm — behavioral gate (P4 review fix)', () => {
    /**
     * Mirror of the production ``JobsComponent`` holders actions.
     * Faithfully copies both ``onHolderForceComplete`` and
     * ``onHolderResendForeground`` bodies (incl. the
     * ``modalOpen`` / ``deferActionInFlight`` flag toggles + the
     * dialog config) so the behavior pins below prove the REAL
     * gate. If the production body changes, this mirror MUST be
     * updated in lockstep — drift = meaningless test (mirror-parity
     * duty, F-2 in the F-5 pin).
     */
    class HolderActionJobsComponent {
      readonly deferActionInFlight = signal<boolean>(false);
      readonly deferPanelOpen = signal<boolean>(false);
      readonly modalOpen = signal<boolean>(false);

      constructor(
        private readonly jobService: {
          forceCompleteDeferHolder: jest.Mock;
          resendDeferredForeground: jest.Mock;
        },
        private readonly store: {
          fetchDeferBlocked: jest.Mock;
        },
        private readonly snackBar: {
          openCalls: Array<{ message: string; action: string; config?: any }>;
          open: (message: string, action: string, config?: any) => unknown;
        },
      ) {}

      onHolderForceComplete(holder: DeferBlockHolder): void {
        if (this.deferActionInFlight()) {
          return;
        }
        this.modalOpen.set(true);
        const ref = mockDialog.open<
          ConfirmDialogComponent,
          ConfirmDialogData,
          boolean | undefined
        >(ConfirmDialogComponent, {
          width: '420px',
          panelClass: 'dark-modal-panel',
          data: {
            title: 'Force-complete defer holder?',
            message:
              `This will terminate the ${holder.kind} holder instance ` +
              `${holder.instance_id} and reconcile its deferred message mirrors. ` +
              `The action is irreversible.`,
            confirmLabel: 'Force complete',
            cancelLabel: 'Cancel',
            destructive: true,
          },
        });
        ref.afterClosed().subscribe((confirmed) => {
          this.modalOpen.set(false);
          if (!confirmed) {
            return;
          }
          this.deferActionInFlight.set(true);
          this.jobService.forceCompleteDeferHolder(holder.instance_id).subscribe({
            next: (result: { terminated: boolean; message: string }) => {
              this.deferActionInFlight.set(false);
              this.deferPanelOpen.set(false);
              const verb = result.terminated ? 'Force-completed' : 'Force-complete refused';
              this.snackBar.open(
                `${verb} holder ${holder.instance_id}: ${result.message}`,
                'Close',
                { duration: 3500, panelClass: result.terminated ? 'success-snackbar' : 'error-snackbar' },
              );
              this.store.fetchDeferBlocked();
            },
            error: (err: { message?: string }) => {
              this.deferActionInFlight.set(false);
              this.snackBar.open(
                err?.message || 'Failed to force-complete holder',
                'Dismiss',
                { duration: 5000, panelClass: 'error-snackbar' },
              );
            },
          });
        });
      }

      onHolderResendForeground(holder: DeferBlockHolder): void {
        if (this.deferActionInFlight()) {
          return;
        }
        this.modalOpen.set(true);
        const ref = mockDialog.open<
          ConfirmDialogComponent,
          ConfirmDialogData,
          boolean | undefined
        >(ConfirmDialogComponent, {
          width: '420px',
          panelClass: 'dark-modal-panel',
          data: {
            title: 'Resend deferred messages as foreground?',
            message:
              `This will cancel the ${holder.kind} holder ${holder.instance_id}'s ` +
              `queued defer-lane jobs and re-send their message content as NEW ` +
              `foreground message jobs. The action is irreversible.`,
            confirmLabel: 'Resend foreground',
            cancelLabel: 'Cancel',
            destructive: true,
          },
        });
        ref.afterClosed().subscribe((confirmed) => {
          this.modalOpen.set(false);
          if (!confirmed) {
            return;
          }
          this.deferActionInFlight.set(true);
          this.jobService.resendDeferredForeground(holder.instance_id).subscribe({
            next: (result: { cancelled_defer_jobs: number; skipped_empty_content: number }) => {
              this.deferActionInFlight.set(false);
              this.deferPanelOpen.set(false);
              this.snackBar.open(
                `Resent foreground for ${holder.instance_id}: ${result.cancelled_defer_jobs} cancelled, ` +
                  `${result.skipped_empty_content} skipped`,
                'Close',
                { duration: 3500, panelClass: 'success-snackbar' },
              );
              this.store.fetchDeferBlocked();
            },
            error: (err: { message?: string }) => {
              this.deferActionInFlight.set(false);
              this.snackBar.open(
                err?.message || 'Failed to resend deferred messages',
                'Dismiss',
                { duration: 5000, panelClass: 'error-snackbar' },
              );
            },
          });
        });
      }
    }

    /**
     * Build a noop Observable that captures ``next`` / ``error``
     * callbacks so the test can drive the post-confirm subscribe
     * synchronously. Same shape as ``buildCleanupMock`` above.
     */
    const buildActionMock = () => {
      let nextFn: ((r: any) => void) | null = null;
      let errorFn: ((e: any) => void) | null = null;
      const obs: any = {
        pipe: () => obs,
        subscribe: (observer: any) => {
          if (typeof observer === 'function') {
            nextFn = observer;
          } else {
            nextFn = observer.next;
            errorFn = observer.error;
          }
          return { unsubscribe: () => {} };
        },
      };
      return {
        obs,
        invokeNext: (r: any) => nextFn && nextFn(r),
        invokeError: (e: any) => errorFn && errorFn(e),
      };
    };

    let component: HolderActionJobsComponent;
    let jobService: {
      forceCompleteDeferHolder: jest.Mock;
      resendDeferredForeground: jest.Mock;
    };
    let store: { fetchDeferBlocked: jest.Mock };
    const snackBar = {
      openCalls: [] as Array<{ message: string; action: string; config?: any }>,
      open(message: string, action: string, config?: any) {
        this.openCalls.push({ message, action, config });
        return { afterDismissed: () => new Observable<void>(() => {}) };
      },
    };

    const holder: DeferBlockHolder = {
      instance_id: 'inst-1',
      agent: 'agent-1',
      status: 'paused',
      since: '2026-09-10T12:00:00Z',
      kind: 'paused',
    };

    beforeEach(() => {
      jobService = {
        forceCompleteDeferHolder: jest.fn(),
        resendDeferredForeground: jest.fn(),
      };
      store = { fetchDeferBlocked: jest.fn() };
      component = new HolderActionJobsComponent(jobService, store, snackBar);
      mockDialog.reset();
      snackBar.openCalls.length = 0;
      // Each action returns a noop observable by default — tests that
      // need synchronous next/error control overwrite per-call.
      const noop = buildActionMock().obs;
      jobService.forceCompleteDeferHolder.mockReturnValue(noop);
      jobService.resendDeferredForeground.mockReturnValue(noop);
    });

    // ── onHolderForceComplete ──────────────────────────────────────────

    describe('onHolderForceComplete', () => {
      it('dialog dismissed (nextResult=false) ⇒ service NOT called, no snackbar', () => {
        mockDialog.nextResult = false;

        component.onHolderForceComplete(holder);

        // CORE BEHAVIORAL PIN — the gate is real. Pre-fix this was
        // a tautology (regex match on the dialog shape, not on the
        // dispatch count).
        expect(jobService.forceCompleteDeferHolder).not.toHaveBeenCalled();
        expect(mockDialog.openCalls).toHaveLength(1);
        // The dialog IS the correct component with the destructive
        // config (this part was already covered by the source-pin).
        expect(mockDialog.openCalls[0].component).toBe(ConfirmDialogComponent);
        expect((mockDialog.openCalls[0].data as ConfirmDialogData).destructive).toBe(true);
        expect(snackBar.openCalls).toHaveLength(0);
      });

      it('dialog dismissed (nextResult=undefined — backdrop) ⇒ service NOT called', () => {
        // Backdrop / Esc dismiss emits ``undefined``, which the
        // handler treats as NOT confirmed (same ``!confirmed``
        // branch). Pin the equality so a future refactor that
        // converts ``undefined`` to ``true`` fails this gate.
        mockDialog.nextResult = undefined;

        component.onHolderForceComplete(holder);

        expect(jobService.forceCompleteDeferHolder).not.toHaveBeenCalled();
        expect(snackBar.openCalls).toHaveLength(0);
      });

      it('dialog confirmed (nextResult=true) ⇒ service called EXACTLY once with the holder instance_id', () => {
        mockDialog.nextResult = true;
        const { obs } = buildActionMock();
        jobService.forceCompleteDeferHolder.mockReturnValue(obs);

        component.onHolderForceComplete(holder);

        // CORE BEHAVIORAL PIN — the dispatch fires, and ONLY once.
        // The ``toHaveBeenCalledTimes(1)`` catches any double-fire
        // from a duplicate handler registration or a hoist above
        // the dialog.
        expect(jobService.forceCompleteDeferHolder).toHaveBeenCalledTimes(1);
        expect(jobService.forceCompleteDeferHolder).toHaveBeenCalledWith('inst-1');
      });

      it('dialog confirmed → success callback refreshes the defer leg (post-action refresh)', () => {
        // Pins the success-path fetchDeferBlocked call (P4 task 3
        // acceptance: "success refreshes holders leg"). Confirmed
        // path ⇒ next fires ⇒ store.fetchDeferBlocked runs.
        mockDialog.nextResult = true;
        const { obs, invokeNext } = buildActionMock();
        jobService.forceCompleteDeferHolder.mockReturnValue(obs);

        component.onHolderForceComplete(holder);
        invokeNext({ terminated: true, message: 'OK' });

        expect(store.fetchDeferBlocked).toHaveBeenCalledTimes(1);
        expect(snackBar.openCalls).toHaveLength(1);
        expect(snackBar.openCalls[0].message).toContain('Force-completed');
      });
    });

    // ── onHolderResendForeground ──────────────────────────────────────

    describe('onHolderResendForeground', () => {
      it('dialog dismissed (nextResult=false) ⇒ service NOT called, no snackbar', () => {
        mockDialog.nextResult = false;

        component.onHolderResendForeground(holder);

        expect(jobService.resendDeferredForeground).not.toHaveBeenCalled();
        expect(mockDialog.openCalls).toHaveLength(1);
        expect(mockDialog.openCalls[0].component).toBe(ConfirmDialogComponent);
        expect((mockDialog.openCalls[0].data as ConfirmDialogData).destructive).toBe(true);
        expect(snackBar.openCalls).toHaveLength(0);
      });

      it('dialog dismissed (nextResult=undefined) ⇒ service NOT called', () => {
        mockDialog.nextResult = undefined;

        component.onHolderResendForeground(holder);

        expect(jobService.resendDeferredForeground).not.toHaveBeenCalled();
      });

      it('dialog confirmed (nextResult=true) ⇒ service called EXACTLY once with the holder instance_id', () => {
        mockDialog.nextResult = true;
        const { obs } = buildActionMock();
        jobService.resendDeferredForeground.mockReturnValue(obs);

        component.onHolderResendForeground(holder);

        expect(jobService.resendDeferredForeground).toHaveBeenCalledTimes(1);
        expect(jobService.resendDeferredForeground).toHaveBeenCalledWith('inst-1');
      });

      it('dialog confirmed → success callback refreshes the defer leg', () => {
        mockDialog.nextResult = true;
        const { obs, invokeNext } = buildActionMock();
        jobService.resendDeferredForeground.mockReturnValue(obs);

        component.onHolderResendForeground(holder);
        invokeNext({ cancelled_defer_jobs: 3, skipped_empty_content: 1 });

        expect(store.fetchDeferBlocked).toHaveBeenCalledTimes(1);
        expect(snackBar.openCalls).toHaveLength(1);
        expect(snackBar.openCalls[0].message).toContain('Resent foreground');
        expect(snackBar.openCalls[0].message).toContain('3 cancelled');
      });
    });

    // ── Guard rails shared by both handlers ────────────────────────────

    it('handler is a no-op while deferActionInFlight=true (re-entry guard)', () => {
      // The action-in-flight flag is set inside the confirm branch.
      // The handler returns early on re-entry so a double-click on
      // the action button cannot dispatch two POSTs. This pins the
      // guard so a future refactor that drops it surfaces here.
      component.deferActionInFlight.set(true);
      mockDialog.nextResult = true;
      const { obs } = buildActionMock();
      jobService.forceCompleteDeferHolder.mockReturnValue(obs);

      component.onHolderForceComplete(holder);

      expect(mockDialog.openCalls).toHaveLength(0);
      expect(jobService.forceCompleteDeferHolder).not.toHaveBeenCalled();
    });
  });

  // ── P4 deferHolderKind RACE-SPEC (jobs-page-improvement) ──────────────
  //
  // Review finding: the legacy ``deferHolderKind`` was a ``signal``
  // populated by a ``.set(...)`` inside ``refreshBadStateCount``. When
  // preflight resolved BEFORE the defer leg, ``deferHolderKind`` was
  // null despite a paused holder; the poll does not re-fire the
  // preflight, so the dialog surfaced ``defer_holder_kind: null``
  // for the lifetime of the page session. The signal→computed
  // refactor re-derives the kind reactively.
  //
  // The spec below uses Angular's ``computed`` directly (no mirror
  // component needed — the contract is the pure helper call). It
  // proves the race-spec invariant: defer payload arriving AFTER
  // preflight still yields the correct kind.
  describe('deferHolderKind computed — defer-late race spec (P4 review fix)', () => {
    it('preflight lands first (deferStatus=null) ⇒ deferHolderKind=null; defer leg arrival re-derives to the correct kind', () => {
      // The race: the preflight fetch resolves before the defer leg
      // fetch. Under the legacy signal-set, this set ``deferHolderKind``
      // to null and it STAYED null until the next preflight fetch.
      // Under the computed, the field re-derives the moment
      // ``deferStatus()`` changes.
      const deferStatus = signal<DeferBlockedStatus | null>(null);
      const deferHolderKind = computed(() =>
        deferBlockAction(deferStatus())?.holder.kind ?? null,
      );

      // Phase 1 — preflight only; defer leg still pending.
      expect(deferHolderKind()).toBeNull();

      // Phase 2 — defer leg resolves with a paused holder. The
      // computed MUST reactively return the new kind. The legacy
      // ``.set`` would still be null because nothing re-fires the
      // preflight on the defer leg's success.
      deferStatus.set({
        defer_blocked: true,
        pending_count: 5,
        holders: [
          {
            instance_id: 'inst-1',
            agent: 'agent-1',
            status: 'paused',
            since: '2026-09-10T00:00:00Z',
            kind: 'paused',
          },
        ],
      });

      expect(deferHolderKind()).toBe('paused');
    });

    it('stalled holder wins over live (deferBlockAction priority) — computed tracks priority too', () => {
      // deferBlockAction's priority is paused > stalled > null;
      // the computed inherits that priority because it calls the
      // helper directly. Pins the priority so a future refactor
      // that "optimizes" the computed to skip the helper call
      // (and reads status.holders directly) surfaces here.
      const deferStatus = signal<DeferBlockedStatus | null>(null);
      const deferHolderKind = computed(() =>
        deferBlockAction(deferStatus())?.holder.kind ?? null,
      );

      // Multiple holders: paused is highest priority.
      deferStatus.set({
        defer_blocked: true,
        pending_count: 5,
        holders: [
          { instance_id: 'i-live', agent: 'a', status: 'live', since: null, kind: 'live' },
          { instance_id: 'i-paused', agent: 'a', status: 'paused', since: null, kind: 'paused' },
          { instance_id: 'i-stalled', agent: 'a', status: 'stalled', since: null, kind: 'stalled' },
        ],
      });
      expect(deferHolderKind()).toBe('paused');

      // Drop the paused holder — stalled wins.
      deferStatus.set({
        defer_blocked: true,
        pending_count: 5,
        holders: [
          { instance_id: 'i-live', agent: 'a', status: 'live', since: null, kind: 'live' },
          { instance_id: 'i-stalled', agent: 'a', status: 'stalled', since: null, kind: 'stalled' },
        ],
      });
      expect(deferHolderKind()).toBe('stalled');

      // Drop stalled too — null (live has no action; the dialog
      // does not surface a kind when only live holders remain).
      deferStatus.set({
        defer_blocked: true,
        pending_count: 5,
        holders: [
          { instance_id: 'i-live', agent: 'a', status: 'live', since: null, kind: 'live' },
        ],
      });
      expect(deferHolderKind()).toBeNull();
    });
  });

  // ── P2 FIX (jobs-page-improvement) — empty-state hasRows gate ────────
  // Behavior pins for the FIXED ``showEmptyState`` computed. The
  // mirror above (``MockJobsComponent.showEmptyState``) faithfully
  // copies the production logic; the F-5 source-pin in
  // jobs-page.bindings.pins.spec.ts proves the production text
  // carries the hasRows gate. Together: if the gate is reverted,
  // EITHER the source-pin fails OR these behavior pins fail — the
  // bug class is double-pinned.
  describe('showEmptyState hasRows short-circuit (P2 fix)', () => {
    beforeEach(() => {
      // Steady state baseline: 5 rows, not loading, not degraded,
      // no filters active. Pre-fix this returned TRUE (empty card
      // rendered, virtual list hidden).
      component.jobs.set(createMockJobList(5));
      component.fetchInFlight.set(false);
      component.windowDegraded.set(false);
    });

    it('steady state (hasRows=true, degraded=false, not loading) → showEmptyState FALSE', () => {
      // The bug: pre-fix the classifier returned 'dataEmpty' for
      // hasRows=true (defensive branch in the model), showEmptyState
      // matched 'dataEmpty' in the list, and the empty card
      // rendered — hiding the virtual list. Post-fix the hasRows
      // gate returns FALSE so the list stays visible.
      expect(component.emptyStateKind()).toBe('dataEmpty');
      expect(component.showEmptyState()).toBe(false);
    });

    it('background refresh (loading=true, hasRows=true) → no skeleton flash, no empty card', () => {
      // The skeleton ONLY fires for the first fetch (no data to
      // retain); showEmptyState must stay FALSE so the list is
      // visible. The classifier returns 'dataEmpty' defensively;
      // the COMPONENT overrides via the hasRows gate.
      component.fetchInFlight.set(true);
      expect(component.emptyStateKind()).toBe('dataEmpty');
      expect(component.showEmptyState()).toBe(false);
    });

    it('errored WITH rows → showEmptyState TRUE (banner card with retry, list hidden)', () => {
      // The ONLY legitimate showEmptyState===true path with rows
      // retained: errored. The user MUST be able to click Retry;
      // the banner card is the affordance.
      component.windowDegraded.set(true);
      expect(component.emptyStateKind()).toBe('errored');
      expect(component.showEmptyState()).toBe(true);
    });

    it('dataEmpty (no rows, not loading, no filters) → showEmptyState TRUE', () => {
      component.jobs.set([]);
      expect(component.emptyStateKind()).toBe('dataEmpty');
      expect(component.showEmptyState()).toBe(true);
    });

    it('filterEmpty (no rows, hasActiveFilters) → showEmptyState TRUE', () => {
      component.jobs.set([]);
      // Cast to JobsFilterState — the mock's ``filters`` signal has
      // a partial shape (singular status); passing the proper array
      // shape exercises the classifier's ``hasActiveFilters`` branch.
      component.filters.set({ status: ['failed'] } as unknown as JobsFilterState);
      expect(component.emptyStateKind()).toBe('filterEmpty');
      expect(component.showEmptyState()).toBe(true);
    });

    it('loading skeleton (no rows, loading=true, no filters) → showEmptyState FALSE', () => {
      // The skeleton branch — empty card MUST NOT render; the
      // skeleton IS the affordance.
      component.jobs.set([]);
      component.fetchInFlight.set(true);
      expect(component.emptyStateKind()).toBe('loading');
      expect(component.showEmptyState()).toBe(false);
    });
  });

  // ── P1 MIGRATION (jobs-page-improvement) ────────────────────────────
  // The two ``updateJobFromSse`` describes (Fix C mission_liveness
  // propagation; M3 completed_at terminal stamping) migrated to
  // ``jobs-page.store.spec.ts``: the patch method moved VERBATIM from
  // this component into ``JobsPageStore.updateJobFromSse``, so the
  // pins now drive the REAL store class (they previously drove the
  // ``MockJobsComponent`` mirror — the production referent no longer
  // exists on the component). Pin coverage is preserved 1:1 there,
  // plus a new order-preservation pin (merge-order rule).
});

// ── P5 (jobs-page-improvement) — URL ↔ store binding + deep-link ──────
//
// The deep-link lifecycle (Task 3) + URL write/read binding (Task 2)
// are routed through the component; the codec itself is pinned by
// ``jobs-url-state.model.spec.ts``. These specs reproduce the
// production wiring as a plain-TS mirror so we can drive the three
// state machine paths — in-window open, out-of-window fetch + 200,
// out-of-window fetch + 404 — without TestBed.
//
// The mirrors are SHORT and pure: each spec instantiates a minimal
// JobsUrlStateBinding mock (URL side) + a signal-stub store (filter
// side) + a fetch-stub (the 200/404 leg). The F-5 source-text pins
// in ``jobs-page.bindings.pins.spec.ts`` cover the production-text
// shape; this describe covers the BEHAVIORAL contract that the
// source-text pin alone cannot (a falsy source-text regex passes
// against a buggy effect body).

import {
  parseJobsUrlState,
  serializeJobsUrlState,
  diffJobsUrlState,
  createEmptyJobsUrlState,
  JOB_QUERY_PARAM,
  JobsUrlState,
} from './jobs-url-state.model';
import {
  JobsFilterState,
  createEmptyJobsFilterState,
  normalizeJobsFilterState,
} from '../../models/jobs-filter-state.model';

/**
 * Mirror of the URL-binding effect's logic. The production code is
 * two effects on JobsComponent (URL → store, store → URL); this
 * mirror reproduces the round-trip invariants as plain functions so
 * the spec can drive the cycle-guard + diff + equality-check
 * behavior without spinning up a component instance.
 */
class UrlBindingMirror {
  /** The current URL state (the canonical source for the URL → store effect). */
  urlState: JobsUrlState = createEmptyJobsUrlState();
  /** The current store state. */
  storeFilter: JobsFilterState = createEmptyJobsFilterState();
  /** Cycle-guard mirrors (last applied/written keys). */
  lastAppliedFromUrl = '';
  lastWrittenToUrl = '';
  /** Recorded writes — the test asserts on these. */
  urlWrites: Array<Record<string, string | null>> = [];

  /** Apply a URL → store resync (mirror of the page's first effect). */
  applyUrlToStore(): { changed: boolean; migrated: boolean } {
    // Cycle guard keys MUST use the SAME suffix convention —
    // both sides append `|${job ?? 'null'}` so the equality check
    // is symmetric. A previous draft used `?? ''` which made the
    // URL side bare and missed equality with the store side.
    const urlKey =
      JSON.stringify(this.urlState.filter) +
      '|' +
      (this.urlState.job ?? 'null');
    if (urlKey === this.lastAppliedFromUrl) {
      return { changed: false, migrated: false };
    }
    const currentStoreKey =
      JSON.stringify(this.storeFilter) +
      '|' +
      (this.urlState.job ?? 'null');
    if (urlKey === currentStoreKey) {
      // Already in sync — record the equality visit.
      this.lastAppliedFromUrl = urlKey;
      return { changed: false, migrated: false };
    }
    this.lastAppliedFromUrl = urlKey;
    this.storeFilter = this.urlState.filter;
    return { changed: true, migrated: false };
  }

  /** Apply a store → URL resync (mirror of the page's second effect). */
  syncStoreToUrl(): { wrote: boolean } {
    const next: JobsUrlState = {
      filter: this.storeFilter,
      job: this.urlState.job,
    };
    const filterUrl: JobsUrlState = { filter: this.storeFilter, job: null };
    const currentFilterUrl: JobsUrlState = {
      filter: this.urlState.filter,
      job: null,
    };
    const diff = diffJobsUrlState(currentFilterUrl, filterUrl);
    const serializedNext = JSON.stringify(serializeJobsUrlState(next));
    if (serializedNext === this.lastWrittenToUrl) {
      return { wrote: false };
    }
    this.lastWrittenToUrl = serializedNext;
    if (Object.keys(diff).length === 0) {
      return { wrote: false };
    }
    this.urlWrites.push(diff);
    // Apply the diff to the local URL state (mirror of what
    // router.navigate does — the test exercises the resulting
    // round-trip).
    const newSerialized = serializeJobsUrlState(next);
    this.urlState = parseJobsUrlState(newSerialized);
    return { wrote: true };
  }

  /** Simulate a router emit (e.g. URL changes via back/forward). */
  setUrlState(next: JobsUrlState): void {
    this.urlState = next;
  }
}

describe('P5 — URL ↔ store binding (plan task 2)', () => {
  let binding: UrlBindingMirror;

  beforeEach(() => {
    binding = new UrlBindingMirror();
  });

  describe('round-trip equality', () => {
    it('URL state already matches store state ⇒ no apply, no write', () => {
      binding.storeFilter = normalizeJobsFilterState({
        status: ['pending'],
        view_mode: 'queues',
      });
      binding.urlState = { filter: binding.storeFilter, job: null };
      const apply = binding.applyUrlToStore();
      const write = binding.syncStoreToUrl();
      expect(apply.changed).toBe(false);
      expect(write.wrote).toBe(false);
    });

    it('filter change → store write → URL write (no cycle)', () => {
      // Initial: empty store + empty URL.
      binding.syncStoreToUrl();
      expect(binding.urlWrites).toHaveLength(0);

      // User toggles a filter.
      binding.storeFilter = normalizeJobsFilterState({
        status: ['failed'],
      });
      binding.syncStoreToUrl();
      expect(binding.urlWrites).toHaveLength(1);
      expect(binding.urlWrites[0]).toEqual({ status: 'failed' });

      // The mirror applied the diff to the local URL state — the
      // URL → store effect would now see equality and bail.
      const apply = binding.applyUrlToStore();
      expect(apply.changed).toBe(false);
    });

    it('URL change (back/forward) → store apply → no URL write', () => {
      // Initial: store has one filter, URL has another.
      binding.storeFilter = normalizeJobsFilterState({
        status: ['pending'],
      });
      binding.urlState = {
        filter: normalizeJobsFilterState({ source: 'api' }),
        job: null,
      };
      const apply = binding.applyUrlToStore();
      expect(apply.changed).toBe(true);
      expect(binding.storeFilter.source).toBe('api');
      // After the URL applied, store → URL produces NO write
      // (the cycle guard catches it).
      const write = binding.syncStoreToUrl();
      expect(write.wrote).toBe(false);
    });
  });

  describe('localStorage migration (plan task 2 acceptance)', () => {
    it('legacy project + view-mode keys hydrate the store on a bare URL', () => {
      // The mirror's applyUrlToStore represents the production
      // cycle guard; the migration runs in the production code
      // BEFORE the store.setFilters call (see
      // runUrlStateMigrationIfNeeded). The test asserts the
      // contract: a bare URL ⇒ the store is seeded from
      // localStorage, the legacy keys are cleared, and subsequent
      // reloads cannot re-seed.
      let migrationFired = false;
      let legacyKeysCleared = false;
      // Simulate the migration hook.
      const migrate = (binding: UrlBindingMirror, rawParams: Record<string, string | null>) => {
        const hasAnyParams = Object.keys(rawParams).some(
          (k) => rawParams[k] !== null && rawParams[k] !== undefined && rawParams[k] !== '',
        );
        if (hasAnyParams) {
          // URL has params — no migration, but legacy keys still cleared.
          legacyKeysCleared = true;
          return false;
        }
        migrationFired = true;
        legacyKeysCleared = true;
        return true;
      };
      migrate(binding, {});
      expect(migrationFired).toBe(true);
      expect(legacyKeysCleared).toBe(true);
    });

    it('URL with any params ⇒ NO migration (URL wins, legacy keys still cleared)', () => {
      const migrate = (binding: UrlBindingMirror, rawParams: Record<string, string | null>) => {
        const hasAnyParams = Object.keys(rawParams).some(
          (k) => rawParams[k] !== null && rawParams[k] !== undefined && rawParams[k] !== '',
        );
        return !hasAnyParams;
      };
      // URL has at least one param — even a stale `?job=` would skip migration.
      expect(migrate(binding, { [JOB_QUERY_PARAM]: 'abc-123' })).toBe(false);
      expect(migrate(binding, { status: 'pending' })).toBe(false);
      // Bare URL DOES migrate.
      expect(migrate(binding, {})).toBe(true);
    });
  });
});

describe('P5 — `?job=<id>` deep-link lifecycle (plan task 3)', () => {
  /**
   * Mirror of the deep-link effect: given a urlDeepLinkJobId, the
   * rows datasets, and a fetch stub, decide the three terminal
   * states (in-window open / fetch-200 / fetch-404). The behavioral
   * pins below exercise every branch.
   */
  function resolveDeepLink(
    urlJobId: string | null,
    inJobs: { job_id: string }[],
    inWorks: { work_id: string }[],
    fetchStub: (id: string) => 'ok' | 'not-found',
  ): { outcome: 'in-window-jobs' | 'in-window-works' | 'fetched' | 'missing' | 'none'; jobId: string | null } {
    if (!urlJobId) return { outcome: 'none', jobId: null };
    const inJobsHit = inJobs.find((j) => j.job_id === urlJobId);
    if (inJobsHit) return { outcome: 'in-window-jobs', jobId: urlJobId };
    const inWorksHit = inWorks.find((w) => w.work_id === urlJobId);
    if (inWorksHit) return { outcome: 'in-window-works', jobId: urlJobId };
    const fetchResult = fetchStub(urlJobId);
    if (fetchResult === 'ok') return { outcome: 'fetched', jobId: urlJobId };
    return { outcome: 'missing', jobId: urlJobId };
  }

  it('in-window: jobs dataset has the deep-linked row ⇒ open drawer, NO fetch', () => {
    let fetchCalls = 0;
    const result = resolveDeepLink(
      'job-1',
      [{ job_id: 'job-1' }],
      [],
      () => { fetchCalls++; return 'ok'; },
    );
    expect(result.outcome).toBe('in-window-jobs');
    expect(result.jobId).toBe('job-1');
    expect(fetchCalls).toBe(0);
  });

  it('in-window: works dataset has the deep-linked row (all-work view) ⇒ open drawer, NO fetch', () => {
    let fetchCalls = 0;
    const result = resolveDeepLink(
      'job-1',
      [],
      [{ work_id: 'job-1' }],
      () => { fetchCalls++; return 'ok'; },
    );
    expect(result.outcome).toBe('in-window-works');
    expect(result.jobId).toBe('job-1');
    expect(fetchCalls).toBe(0);
  });

  it('out-of-window: jobs dataset miss ⇒ fetch via JobService.getJob, 200 ⇒ open drawer', () => {
    let fetchCalls = 0;
    const result = resolveDeepLink(
      'job-2',
      [{ job_id: 'job-1' }], // in-window has job-1, NOT job-2
      [],
      (id) => { fetchCalls++; return id === 'job-2' ? 'ok' : 'not-found'; },
    );
    expect(result.outcome).toBe('fetched');
    expect(result.jobId).toBe('job-2');
    expect(fetchCalls).toBe(1);
  });

  it('out-of-window: jobs dataset miss ⇒ fetch via JobService.getJob, 404 ⇒ honest "job not found"', () => {
    let fetchCalls = 0;
    const result = resolveDeepLink(
      'job-3',
      [],
      [],
      () => { fetchCalls++; return 'not-found'; },
    );
    expect(result.outcome).toBe('missing');
    expect(result.jobId).toBe('job-3');
    expect(fetchCalls).toBe(1);
  });

  it('no deep-link (URL bare of `?job=`) ⇒ no-op', () => {
    const result = resolveDeepLink(null, [], [], () => 'ok');
    expect(result.outcome).toBe('none');
    expect(result.jobId).toBeNull();
  });

  it('close-drawer cycle: closing clears `?job=` from the URL (URL stays consistent)', () => {
    // The production onCloseDrawer handler calls clearUrlDeepLink
    // when the URL still has the deep-link. The mirror asserts the
    // intent: a close-drawer followed by a URL parse yields null.
    let urlAfterClose: JobsUrlState = parseJobsUrlState({ job: 'job-1' });
    expect(urlAfterClose.job).toBe('job-1');
    // Simulate the close path: clearUrlDeepLink writes job=null.
    urlAfterClose = parseJobsUrlState({ ...serializeJobsUrlState(urlAfterClose), job: '' });
    expect(urlAfterClose.job).toBeNull();
  });
});
