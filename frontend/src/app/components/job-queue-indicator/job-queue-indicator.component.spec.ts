import { signal, computed } from '@angular/core';
import { Job, JobStatus } from '../../models/job.model';
import { createMockJob, JobStatus as HelperJobStatus } from '../../testing/job-test-helpers';

/**
 * Logic-mirror of JobQueueIndicatorComponent.
 *
 * This project does NOT use Angular TestBed for component tests. Instead, we
 * replicate the component's signal/computed logic in a plain TS class and
 * test it directly — same pattern as job-detail-drawer.component.spec.ts.
 *
 * The mirror exposes the private helpers (isRunningStatus, isPendingStatus,
 * isTerminalStatus, shortenId) that the real component keeps module-private
 * (or class-private) so the assertions below can exercise them directly. We
 * also expose the ``runningJobs`` computed and a synthetic ``recentFiltered``
 * computed that mirrors the sort/filter/cap behaviour of ``fetchJobs``.
 */
class MockJobQueueIndicatorComponent {
  /** Raw active jobs (running + pending) — mirrors ``activeJobs``. */
  private readonly activeJobs = signal<Job[]>([]);

  /** Raw recent jobs (terminal) — mirrors ``recentJobs``. */
  private readonly recentJobs = signal<Job[]>([]);

  /** Cached project_id → project name. */
  private readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  // ---------------------------------------------------------------------------
  // Helpers exposed as methods so tests can call them directly.
  // ---------------------------------------------------------------------------

  isRunningStatus(s: JobStatus): boolean {
    return s === 'processing' || (s as string) === 'active';
  }

  isPendingStatus(s: JobStatus): boolean {
    return s === 'pending' || (s as string) === 'queued';
  }

  isTerminalStatus(s: JobStatus): boolean {
    return ['completed', 'failed', 'cancelled', 'dead_letter'].includes(s);
  }

  shortenId(id: string): string {
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  // ---------------------------------------------------------------------------
  // Derived signals — mirror the public computeds on the real component.
  // ---------------------------------------------------------------------------

  runningCount = computed(
    () => this.activeJobs().filter((j) => this.isRunningStatus(j.status)).length
  );

  pendingCount = computed(
    () => this.activeJobs().filter((j) => this.isPendingStatus(j.status)).length
  );

  totalNonTerminal = computed(() => this.runningCount() + this.pendingCount());

  isIdle = computed(() => this.totalNonTerminal() === 0);

  displayText = computed(
    () => `${this.runningCount()}/${this.totalNonTerminal()}`
  );

  runningJobs = computed(() =>
    this.activeJobs().filter((j) => this.isRunningStatus(j.status))
  );

  /**
   * Synthetic computed: terminal-only subset of ``recentJobs``, sorted by
   * ``completed_at`` desc (falling back to ``created_at``) and capped at 10.
   * Mirrors the slice/sort logic in the real component's ``fetchJobs``.
   */
  recentFiltered = computed<Job[]>(() => {
    return this.recentJobs()
      .filter((j) => !this.isPendingStatus(j.status) && !this.isRunningStatus(j.status))
      .sort((a, b) => {
        const aT = a.completed_at ?? a.created_at;
        const bT = b.completed_at ?? b.created_at;
        return bT.localeCompare(aT);
      })
      .slice(0, 10);
  });

  // ---------------------------------------------------------------------------
  // onJobClick navigation helper — mirrors the real component's routing
  // decisions so tests can assert what the parent would have done.
  // ---------------------------------------------------------------------------

  /**
   * Capture of the latest onJobClick inputs (for assertions in tests).
   */
  onJobClickArgs: { project_id: string | null; instance_id: string | null } | null = null;

  onJobClick(job: Job): {
    addTabProjectId: string;
    addTabName: string;
    navigateTo: (string | null)[];
  } {
    const projectKey = job.project_id || 'all';
    const addTabProjectId = job.project_id ?? 'all';
    const addTabName = (job.project_id ?? 'all').slice(0, 8);
    const navigateTo: (string | null)[] = [
      '/projects',
      projectKey,
      'instances',
      job.instance_id,
    ];
    this.onJobClickArgs = {
      project_id: job.project_id,
      instance_id: job.instance_id,
    };
    return { addTabProjectId, addTabName, navigateTo };
  }

  // ---------------------------------------------------------------------------
  // Setters — let tests push data into the signals without poking internals.
  // ---------------------------------------------------------------------------

  setActiveJobs(j: Job[]): void {
    this.activeJobs.set(j);
  }

  setRecentJobs(j: Job[]): void {
    this.recentJobs.set(j);
  }

  setProjectNameMap(m: Map<string | null, string>): void {
    this.projectNameMap.set(m);
  }
}

describe('JobQueueIndicatorComponent Logic', () => {
  let component: MockJobQueueIndicatorComponent;

  beforeEach(() => {
    component = new MockJobQueueIndicatorComponent();
  });

  describe('instantiation', () => {
    it('should create the logic-mirror component', () => {
      expect(component).toBeTruthy();
    });

    it('should default to "0/0" and idle state', () => {
      expect(component.displayText()).toBe('0/0');
      expect(component.isIdle()).toBe(true);
      expect(component.runningCount()).toBe(0);
      expect(component.pendingCount()).toBe(0);
      expect(component.totalNonTerminal()).toBe(0);
    });
  });

  describe('runningCount', () => {
    it('should count only processing jobs', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.runningCount()).toBe(2);
    });

    it('should treat "active" as running via the defensive fallback', () => {
      component.setActiveJobs([
        createMockJob({ status: 'active' as unknown as HelperJobStatus }),
        createMockJob({ status: 'processing' }),
      ]);
      expect(component.runningCount()).toBe(2);
    });

    it('should not count pending or terminal jobs', () => {
      component.setActiveJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'completed' }),
        createMockJob({ status: 'failed' }),
        createMockJob({ status: 'cancelled' }),
        createMockJob({ status: 'dead_letter' }),
      ]);
      expect(component.runningCount()).toBe(0);
    });
  });

  describe('pendingCount', () => {
    it('should count only pending jobs', () => {
      component.setActiveJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'processing' }),
      ]);
      expect(component.pendingCount()).toBe(2);
    });

    it('should treat "queued" as pending via the defensive fallback', () => {
      component.setActiveJobs([
        createMockJob({ status: 'queued' as unknown as HelperJobStatus }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.pendingCount()).toBe(2);
    });

    it('should not count processing or terminal jobs', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'completed' }),
        createMockJob({ status: 'failed' }),
        createMockJob({ status: 'cancelled' }),
        createMockJob({ status: 'dead_letter' }),
      ]);
      expect(component.pendingCount()).toBe(0);
    });
  });

  describe('displayText', () => {
    it('should produce "2/3" with 2 processing and 1 pending', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.displayText()).toBe('2/3');
    });

    it('should produce "0/0" with no active jobs', () => {
      component.setActiveJobs([]);
      expect(component.displayText()).toBe('0/0');
    });

    it('should produce "0/3" with 0 processing and 3 pending', () => {
      component.setActiveJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.displayText()).toBe('0/3');
    });

    it('should ignore terminal jobs in the denominator', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'completed' }),
        createMockJob({ status: 'failed' }),
      ]);
      // Only the processing job contributes; terminal jobs do NOT inflate Y.
      expect(component.displayText()).toBe('1/1');
    });
  });

  describe('isIdle', () => {
    it('should be true when there are no active jobs', () => {
      component.setActiveJobs([]);
      expect(component.isIdle()).toBe(true);
    });

    it('should be true even when recent (terminal) jobs exist', () => {
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.isIdle()).toBe(true);
    });

    it('should be false when there is at least one running job', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      expect(component.isIdle()).toBe(false);
    });

    it('should be false when there is at least one pending job', () => {
      component.setActiveJobs([createMockJob({ status: 'pending' })]);
      expect(component.isIdle()).toBe(false);
    });
  });

  describe('runningJobs (computed subset for panel)', () => {
    it('should include only processing/active jobs', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'r1', status: 'processing' }),
        createMockJob({ job_id: 'p1', status: 'pending' }),
        createMockJob({ job_id: 'r2', status: 'active' as unknown as HelperJobStatus }),
        createMockJob({ job_id: 'c1', status: 'completed' }),
      ]);
      const ids = component.runningJobs().map((j) => j.job_id);
      expect(ids).toEqual(['r1', 'r2']);
    });
  });

  describe('recentFiltered', () => {
    it('should filter out pending and processing jobs (terminal only)', () => {
      component.setRecentJobs([
        createMockJob({ job_id: 'c1', status: 'completed' }),
        createMockJob({ job_id: 'p1', status: 'pending' }),
        createMockJob({ job_id: 'f1', status: 'failed' }),
        createMockJob({ job_id: 'r1', status: 'processing' }),
        createMockJob({ job_id: 'd1', status: 'dead_letter' }),
        createMockJob({ job_id: 'x1', status: 'cancelled' }),
      ]);
      const ids = component.recentFiltered().map((j) => j.job_id);
      expect(ids).toEqual(['c1', 'f1', 'd1', 'x1']);
    });

    it('should sort by completed_at desc, falling back to created_at', () => {
      component.setRecentJobs([
        createMockJob({
          job_id: 'a',
          status: 'completed',
          created_at: '2026-07-20T10:00:00Z',
          completed_at: '2026-07-20T10:05:00Z',
        }),
        createMockJob({
          job_id: 'b',
          status: 'completed',
          created_at: '2026-07-21T10:00:00Z',
          completed_at: '2026-07-21T10:05:00Z',
        }),
        createMockJob({
          job_id: 'c',
          status: 'failed',
          created_at: '2026-07-19T10:00:00Z',
          completed_at: null,
        }),
      ]);
      const ids = component.recentFiltered().map((j) => j.job_id);
      // b (newest completed_at), a, c (oldest created_at fallback).
      expect(ids).toEqual(['b', 'a', 'c']);
    });

    it('should cap results at 10 entries', () => {
      const many = Array.from({ length: 15 }, (_, i) =>
        createMockJob({
          job_id: `j-${i}`,
          status: 'completed',
          created_at: `2026-07-20T10:00:0${i % 10}:00Z`,
          completed_at: `2026-07-20T10:00:0${i % 10}:00Z`,
        })
      );
      component.setRecentJobs(many);
      expect(component.recentFiltered().length).toBe(10);
    });

    it('should return an empty list when there are no terminal jobs', () => {
      component.setRecentJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'processing' }),
      ]);
      expect(component.recentFiltered()).toEqual([]);
    });
  });

  describe('onJobClick', () => {
    it('should open project tab and navigate to instance with project_id', () => {
      const job = createMockJob({
        job_id: 'job-1',
        project_id: 'project-abcdef',
        instance_id: 'inst-xyz',
        status: 'processing',
      });
      const result = component.onJobClick(job);
      expect(result.addTabProjectId).toBe('project-abcdef');
      expect(result.addTabName).toBe('project-'); // first 8 chars
      expect(result.navigateTo).toEqual([
        '/projects',
        'project-abcdef',
        'instances',
        'inst-xyz',
      ]);
      expect(component.onJobClickArgs).toEqual({
        project_id: 'project-abcdef',
        instance_id: 'inst-xyz',
      });
    });

    it('should fall back to "all" tab when project_id is null', () => {
      const job = createMockJob({
        job_id: 'job-2',
        project_id: null,
        instance_id: 'inst-zzz',
        status: 'failed',
      });
      const result = component.onJobClick(job);
      expect(result.addTabProjectId).toBe('all');
      expect(result.addTabName).toBe('all');
      expect(result.navigateTo).toEqual([
        '/projects',
        'all',
        'instances',
        'inst-zzz',
      ]);
    });

    it('should pass null through as the final path segment when instance_id is null', () => {
      const job = createMockJob({
        job_id: 'job-3',
        project_id: 'project-123',
        instance_id: null,
        status: 'completed',
      });
      const result = component.onJobClick(job);
      expect(result.navigateTo.length).toBe(4);
      expect(result.navigateTo[0]).toBe('/projects');
      expect(result.navigateTo[1]).toBe('project-123');
      expect(result.navigateTo[2]).toBe('instances');
      expect(result.navigateTo[3]).toBeNull();
    });
  });

  describe('isRunningStatus', () => {
    it('should return true for "processing"', () => {
      expect(component.isRunningStatus('processing')).toBe(true);
    });

    it('should return true for "active" (defensive fallback)', () => {
      expect(component.isRunningStatus('active' as JobStatus)).toBe(true);
    });

    it('should return false for non-running statuses', () => {
      expect(component.isRunningStatus('pending')).toBe(false);
      expect(component.isRunningStatus('queued' as JobStatus)).toBe(false);
      expect(component.isRunningStatus('completed')).toBe(false);
      expect(component.isRunningStatus('failed')).toBe(false);
      expect(component.isRunningStatus('cancelled')).toBe(false);
      expect(component.isRunningStatus('dead_letter')).toBe(false);
    });
  });

  describe('isPendingStatus', () => {
    it('should return true for "pending"', () => {
      expect(component.isPendingStatus('pending')).toBe(true);
    });

    it('should return true for "queued" (defensive fallback)', () => {
      expect(component.isPendingStatus('queued' as JobStatus)).toBe(true);
    });

    it('should return false for non-pending statuses', () => {
      expect(component.isPendingStatus('processing')).toBe(false);
      expect(component.isPendingStatus('active' as JobStatus)).toBe(false);
      expect(component.isPendingStatus('completed')).toBe(false);
      expect(component.isPendingStatus('failed')).toBe(false);
      expect(component.isPendingStatus('cancelled')).toBe(false);
      expect(component.isPendingStatus('dead_letter')).toBe(false);
    });
  });

  describe('isTerminalStatus', () => {
    it('should return true for terminal states', () => {
      expect(component.isTerminalStatus('completed')).toBe(true);
      expect(component.isTerminalStatus('failed')).toBe(true);
      expect(component.isTerminalStatus('cancelled')).toBe(true);
      expect(component.isTerminalStatus('dead_letter')).toBe(true);
    });

    it('should return false for active states', () => {
      expect(component.isTerminalStatus('pending')).toBe(false);
      expect(component.isTerminalStatus('processing')).toBe(false);
      expect(component.isTerminalStatus('active' as JobStatus)).toBe(false);
      expect(component.isTerminalStatus('queued' as JobStatus)).toBe(false);
    });
  });

  describe('shortenId', () => {
    it('should truncate ids longer than 8 chars with "..."', () => {
      expect(component.shortenId('0123456789abcdef')).toBe('01234567...');
    });

    it('should not truncate ids that are exactly 8 chars', () => {
      expect(component.shortenId('12345678')).toBe('12345678');
    });

    it('should not truncate ids shorter than 8 chars', () => {
      expect(component.shortenId('proj-A')).toBe('proj-A');
    });
  });
});
