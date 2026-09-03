import { signal, computed, Component, input, output } from '@angular/core';
import { Observable } from 'rxjs';
import { Job, JobStatus, JobSource } from '../../models/job.model';
import type { Work } from '../../models/work.model';
import { Project } from '../../models/project.model';
import { createMockJob, createMockJobList } from '../../testing/job-test-helpers';
import { ConfirmDialogComponent } from '../../components/confirm-dialog/confirm-dialog.component';
import { SystemCleanupConfirmDialogComponent } from '../../components/system-cleanup-confirm-dialog/system-cleanup-confirm-dialog.component';

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

  /**
   * Mirror of the real component's ``updateJobFromSse`` — used by the
   * SSE propagation specs. Keeps the SAME present-as-null contract
   * for ``job_type`` and ``mission_liveness`` as the real method:
   * key absent → keep previous value, key present + null → clear.
   * See ``jobs.component.ts:updateJobFromSse`` for the canonical
   * implementation.
   */
  updateJobFromSse(status: import('../../models/job.model').JobEventPayload): void {
    const nextJobType: Job['job_type'] =
      'job_type' in status
        ? (status.job_type ?? null) as Job['job_type']
        : undefined;
    const nextMissionLiveness: Job['mission_liveness'] =
      'mission_liveness' in status
        ? (status.mission_liveness ?? null)
        : undefined;

    this.jobs.update(jobs =>
      jobs.map(job =>
        job.job_id === status.job_id
          ? {
              ...job,
              status: status.status || job.status,
              queue_id: status.queue_id ?? job.queue_id,
              instance_id: status.instance_id || job.instance_id,
              result_summary: status.result_summary || job.result_summary,
              error_message: status.error_message || job.error_message,
              completed_at: status.status === 'completed' || status.status === 'failed'
                ? new Date().toISOString()
                : job.completed_at,
              started_at: status.status === 'processing' && !job.started_at
                ? new Date().toISOString()
                : job.started_at,
              ...(nextJobType !== undefined ? { job_type: nextJobType } : {}),
              ...(nextMissionLiveness !== undefined ? { mission_liveness: nextMissionLiveness } : {}),
            }
          : job
      )
    );

    this.works.update(works =>
      works.map(work =>
        work.work_id === status.job_id
          ? {
              ...work,
              status: status.status || work.status,
              instance_id: status.instance_id ?? work.instance_id,
              result_summary: status.result_summary ?? work.result_summary,
              error: status.error_message ?? work.error,
              ...(nextJobType !== undefined ? { job_type: nextJobType } : {}),
              ...(nextMissionLiveness !== undefined ? { mission_liveness: nextMissionLiveness } : {}),
            }
          : work
      )
    );
  }
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

  describe('filteredJobs computed', () => {
    it('should return all jobs when no filters', () => {
      component.filters.set({});
      expect(component.filteredJobs()).toHaveLength(mockJobs.length);
    });

    it('should filter by status', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending' }),
        createMockJob({ job_id: '2', status: 'completed' }),
        createMockJob({ job_id: '3', status: 'pending' }),
      ]);
      
      component.onStatusFilterChange('pending');

      const filtered = component.filteredJobs();
      expect(filtered.every(j => j.status === 'pending')).toBe(true);
    });

    it('should filter by source', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', source: 'api' }),
        createMockJob({ job_id: '2', source: 'telegram' }),
      ]);
      
      component.onSourceFilterChange('api');

      const filtered = component.filteredJobs();
      expect(filtered.every(j => j.source === 'api')).toBe(true);
    });

    it('should filter by agent_id', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', agent_id: 'developer' }),
        createMockJob({ job_id: '2', agent_id: 'tester' }),
      ]);
      
      component.onAgentFilterChange('developer');

      const filtered = component.filteredJobs();
      expect(filtered.every(j => j.agent_id === 'developer')).toBe(true);
    });

    it('should filter by multiple criteria', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending', source: 'api', agent_id: 'developer' }),
        createMockJob({ job_id: '2', status: 'completed', source: 'api', agent_id: 'developer' }),
        createMockJob({ job_id: '3', status: 'pending', source: 'telegram', agent_id: 'developer' }),
      ]);
      
      component.onStatusFilterChange('pending');
      component.onSourceFilterChange('api');

      const filtered = component.filteredJobs();
      expect(filtered.length).toBe(1);
      expect(filtered[0].job_id).toBe('1');
    });
  });

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

  describe('showDeleted signal', () => {
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

  describe('filteredJobs with deleted jobs', () => {
    it('should hide deleted jobs when showDeleted is false', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending' }),
        createMockJob({ job_id: '2', status: 'completed', deleted_at: '2024-01-15T10:00:00Z' }),
        createMockJob({ job_id: '3', status: 'failed' }),
      ]);
      component.showDeleted.set(false);

      const filtered = component.filteredJobs();

      expect(filtered.length).toBe(2);
      expect(filtered.some(j => j.job_id === '1')).toBe(true);
      expect(filtered.some(j => j.job_id === '2')).toBe(false);
      expect(filtered.some(j => j.job_id === '3')).toBe(true);
    });

    it('should show deleted jobs when showDeleted is true', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending' }),
        createMockJob({ job_id: '2', status: 'completed', deleted_at: '2024-01-15T10:00:00Z' }),
        createMockJob({ job_id: '3', status: 'failed' }),
      ]);
      component.showDeleted.set(true);

      const filtered = component.filteredJobs();

      expect(filtered.length).toBe(3);
      expect(filtered.some(j => j.job_id === '1')).toBe(true);
      expect(filtered.some(j => j.job_id === '2')).toBe(true);
      expect(filtered.some(j => j.job_id === '3')).toBe(true);
    });

    it('should not filter jobs without deleted_at when showDeleted is false', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending' }),
        createMockJob({ job_id: '2', status: 'completed' }), // no deleted_at
      ]);
      component.showDeleted.set(false);

      const filtered = component.filteredJobs();

      expect(filtered.length).toBe(2);
    });

    it('should work with other filters combined', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending', deleted_at: '2024-01-15T10:00:00Z' }),
        createMockJob({ job_id: '2', status: 'pending' }),
        createMockJob({ job_id: '3', status: 'completed', deleted_at: '2024-01-15T10:00:00Z' }),
        createMockJob({ job_id: '4', status: 'completed' }),
      ]);
      component.showDeleted.set(true);
      component.onStatusFilterChange('pending');

      const filtered = component.filteredJobs();

      expect(filtered.length).toBe(2);
      expect(filtered.every(j => j.status === 'pending')).toBe(true);
    });

    it('should filter out deleted jobs and apply status filter', () => {
      component.jobs.set([
        createMockJob({ job_id: '1', status: 'pending', deleted_at: '2024-01-15T10:00:00Z' }),
        createMockJob({ job_id: '2', status: 'pending' }),
        createMockJob({ job_id: '3', status: 'completed', deleted_at: '2024-01-15T10:00:00Z' }),
        createMockJob({ job_id: '4', status: 'completed' }),
      ]);
      component.showDeleted.set(false); // Hide deleted
      component.onStatusFilterChange('pending');

      const filtered = component.filteredJobs();

      expect(filtered.length).toBe(1);
      expect(filtered[0].job_id).toBe('2');
    });
  });

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

  describe('All Work view loadWorks — root_only contract (P-A)', () => {
    /**
     * Mirrors the body of ``JobsComponent.loadWorks`` (the
     * Phase-4 unified-work fetch) just enough to assert that the
     * component hands ``root_only: false`` to ``WorkService.getWork``.
     *
     * The real component is heavy with Angular lifecycle hooks,
     * dialogs, and snackbar wiring; re-declaring just the
     * work-fetch path keeps the test focused and avoids the
     * TestBed setup that would otherwise be needed to exercise the
     * component end-to-end. The URL serialisation guarantee is
     * separately covered by ``work.service.spec.ts``.
     */
    class AllWorkLoadComponent {
      // Captured filters handed to WorkService.getWork.
      public lastFilters: any = undefined;
      // Subscription observers, in case a future test wants to
      // assert on the snackbar side effect.
      public errored = false;

      constructor(private readonly filtersValue: { project_id?: string; status?: any }) {}

      loadWorks(workService: { getWork: jest.Mock }): void {
        const projectId = this.filtersValue.project_id;
        const statusFilter = this.filtersValue.status;
        const filters = {
          project_id: projectId || undefined,
          status:
            statusFilter && statusFilter.length > 0
              ? statusFilter.join(',')
              : undefined,
          // P-A — the All Work view intentionally bypasses the
          // root-only filter so child-instance rows stay visible.
          root_only: false,
        };
        workService.getWork(filters).subscribe({
          next: (works: unknown[]) => {
            this.lastFilters = filters;
          },
          error: () => {
            this.errored = true;
          },
        });
      }
    }

    it('should pass root_only: false to WorkService.getWork (no filters)', () => {
      const workService = { getWork: jest.fn().mockReturnValue({
        subscribe: (obs: any) => obs.next([]),
      }) };
      const component = new AllWorkLoadComponent({});

      component.loadWorks(workService);

      expect(workService.getWork).toHaveBeenCalledTimes(1);
      expect(workService.getWork).toHaveBeenCalledWith({
        project_id: undefined,
        status: undefined,
        root_only: false,
      });
    });

    it('should pass root_only: false alongside a project_id filter', () => {
      const workService = { getWork: jest.fn().mockReturnValue({
        subscribe: (obs: any) => obs.next([]),
      }) };
      const component = new AllWorkLoadComponent({ project_id: 'project-123' });

      component.loadWorks(workService);

      expect(workService.getWork).toHaveBeenCalledWith({
        project_id: 'project-123',
        status: undefined,
        root_only: false,
      });
    });

    it('should pass root_only: false alongside a status filter', () => {
      const workService = { getWork: jest.fn().mockReturnValue({
        subscribe: (obs: any) => obs.next([]),
      }) };
      const component = new AllWorkLoadComponent({
        project_id: 'project-123',
        status: ['pending', 'processing'],
      });

      component.loadWorks(workService);

      expect(workService.getWork).toHaveBeenCalledWith({
        project_id: 'project-123',
        status: 'pending,processing',
        root_only: false,
      });
    });

    it('should drop status when the filter array is empty', () => {
      const workService = { getWork: jest.fn().mockReturnValue({
        subscribe: (obs: any) => obs.next([]),
      }) };
      const component = new AllWorkLoadComponent({ status: [] });

      component.loadWorks(workService);

      expect(workService.getWork).toHaveBeenCalledWith({
        project_id: undefined,
        status: undefined,
        root_only: false,
      });
    });

    it('should always include root_only: false even if other filters are undefined', () => {
      const workService = { getWork: jest.fn().mockReturnValue({
        subscribe: (obs: any) => obs.next([]),
      }) };
      const component = new AllWorkLoadComponent({});

      component.loadWorks(workService);

      // ``mock.calls[0]`` is the array of arguments to the first
      // ``getWork`` call — ``[filters]`` since there's one arg.
      const filtersArg = workService.getWork.mock.calls[0][0];
      // The contract: ``root_only`` is always present and is always
      // exactly ``false`` for the All Work view. If this assertion
      // fails, the user is back to seeing the backend-default
      // root-scoped list — the very thing this fix was meant to
      // prevent.
      expect(filtersArg.root_only).toBe(false);
    });
  });

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

  // ── Fix C (§8.2) — SSE patch propagation ─────────────────────────────
  //
  // Round-1 only patched the works[] path, so a terminal mirror in
  // the Queues view stayed pinned to its stale live chip and the
  // header badge kept counting it as a live mission. These specs
  // drive ``updateJobFromSse`` directly so any future regression
  // that drops the patch or collapses null-vs-absent fails loudly.
  //
  // M3 (mission-class, 2026-09-03) — prose uses ``terminal``
  // (mission-side vocabulary) instead of ``settled`` (transport-
  // receipt vocabulary; belongs only to mirror rows now). The
  // data shape (``mission_liveness``) is unchanged.

  describe('updateJobFromSse — mission_liveness propagation (jobs[] path)', () => {
    function seedMirrorRow(liveness: import('../../models/job.model').MissionLiveness) {
      component.jobs.set([
        createMockJob({
          job_id: 'mirror-1',
          status: 'completed',
          instance_id: 'leader-x',
          job_type: 'message',
          mission_liveness: liveness,
        }),
      ]);
      component.works.set([
        {
          work_id: 'mirror-1',
          kind: 'job',
          status: 'completed',
          instance_id: 'leader-x',
          project_id: null,
          agent_id: 'developer',
          result_summary: null,
          error: null,
          created_at: new Date().toISOString(),
          job_type: 'message',
          mission_liveness: liveness,
        },
      ]);
    }

    it('jobs[] path: terminal mission_liveness in the payload overwrites the live row', () => {
      seedMirrorRow('processing');
      component.updateJobFromSse({
        job_id: 'mirror-1',
        status: 'completed',
        mission_liveness: 'completed',
      });
      expect(component.jobs().find(j => j.job_id === 'mirror-1')!.mission_liveness).toBe('completed');
      // works[] path also patches — same payload, same contract.
      expect(component.works().find(w => w.work_id === 'mirror-1')!.mission_liveness).toBe('completed');
    });

    it('present-as-null: explicit null CLEARS, absent key KEEPS previous value', () => {
      seedMirrorRow('processing');

      // Explicit null → cleared on BOTH paths.
      component.updateJobFromSse({
        job_id: 'mirror-1',
        status: 'completed',
        mission_liveness: null,
      });
      expect(component.jobs().find(j => j.job_id === 'mirror-1')!.mission_liveness).toBeNull();
      expect(component.works().find(w => w.work_id === 'mirror-1')!.mission_liveness).toBeNull();

      // Reset, then payload without the key → previous value survives.
      seedMirrorRow('processing');
      component.updateJobFromSse({
        job_id: 'mirror-1',
        status: 'completed',
        // mission_liveness key ABSENT.
      });
      expect(component.jobs().find(j => j.job_id === 'mirror-1')!.mission_liveness).toBe('processing');
      expect(component.works().find(w => w.work_id === 'mirror-1')!.mission_liveness).toBe('processing');
    });
  });
});
