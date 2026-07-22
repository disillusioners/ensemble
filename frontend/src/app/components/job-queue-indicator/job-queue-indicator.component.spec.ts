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
 * isTerminalStatus) that the real component keeps module-private so the
 * assertions below can exercise them directly. We also expose the
 * ``runningJobs`` computed, the public ``recentJobs`` computed (now
 * defensive via ``isTerminalStatus``), the ``tooltipText`` computed, and
 * captured ``onJobClick`` side-effects so tests can assert navigation +
 * tab-action decisions without instantiating Angular.
 */
class MockJobQueueIndicatorComponent {
  /** Raw active jobs (running + paused + pending) — mirrors ``activeJobs``. */
  private readonly activeJobs = signal<Job[]>([]);

  /**
   * Raw recent jobs — mirrors the private ``allRecentJobs`` signal.
   * The public ``recentJobs`` computed derives its filtered/sorted/capped
   * view from this raw value.
   */
  private readonly allRecentJobs = signal<Job[]>([]);

  /** Cached project_id → project name. */
  private readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  // ---------------------------------------------------------------------------
  // Helpers exposed as methods so tests can call them directly.
  // ---------------------------------------------------------------------------

  isRunningStatus(s: JobStatus): boolean {
    return s === 'processing' || s === 'paused' || (s as string) === 'active';
  }

  isPendingStatus(s: JobStatus): boolean {
    return s === 'pending' || (s as string) === 'queued';
  }

  isTerminalStatus(s: JobStatus): boolean {
    return ['completed', 'failed', 'cancelled', 'dead_letter'].includes(s);
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

  /**
   * Mirror of the real component's tooltip text computed.
   * Format: ``Running: X / Pending: Y``.
   */
  tooltipText = computed(
    () => `Running: ${this.runningCount()} / Pending: ${this.pendingCount()}`
  );

  runningJobs = computed(() =>
    this.activeJobs().filter((j) => this.isRunningStatus(j.status))
  );

  /**
   * Public recent jobs — defensive terminal-only subset of
   * ``allRecentJobs``, sorted by ``completed_at`` desc (falling back to
   * ``created_at``) and capped at 10. Mirrors the public ``recentJobs``
   * computed on the real component.
   */
  recentJobs = computed<Job[]>(() =>
    this.allRecentJobs()
      .filter((j) => this.isTerminalStatus(j.status))
      .sort((a, b) => {
        const aT = a.completed_at ?? a.created_at;
        const bT = b.completed_at ?? b.created_at;
        return bT.localeCompare(aT);
      })
      .slice(0, 10)
  );

  // ---------------------------------------------------------------------------
  // onJobClick side-effect capture — mirrors the real component's routing
  // and tab-decisions so tests can assert what the parent would have done.
  // ---------------------------------------------------------------------------

  /** Captures whether the menu would have been closed (true after onJobClick). */
  menuClosedAfterClick = false;

  /**
   * Last tab action decided by ``onJobClick``. Either an ``add`` (with the
   * resolved name) or ``setActive`` (with the target tab id).
   */
  lastTabAction:
    | { kind: 'add'; project_id: string; name: string }
    | { kind: 'setActive'; tabId: string }
    | null = null;

  /** Last navigation path decided by ``onJobClick``. */
  lastNavigated: (string | null)[] | null = null;

  onJobClick(job: Job): void {
    // 1. Close the menu first — mirrors the real component's ordering.
    this.menuClosedAfterClick = true;

    const projectKey = job.project_id || 'all';
    if (job.project_id) {
      const name =
        this.projectNameMap().get(job.project_id) ?? job.project_id.slice(0, 8);
      this.lastTabAction = { kind: 'add', project_id: job.project_id, name };
    } else {
      this.lastTabAction = { kind: 'setActive', tabId: 'all' };
    }

    // 3. Navigate to specific instance when truthy, otherwise to the
    //    project/all instances list (no null trailing segment).
    this.lastNavigated = job.instance_id
      ? ['/projects', projectKey, 'instances', job.instance_id]
      : ['/projects', projectKey, 'instances'];
  }

  // ---------------------------------------------------------------------------
  // Setters — let tests push data into the signals without poking internals.
  // ---------------------------------------------------------------------------

  setActiveJobs(j: Job[]): void {
    this.activeJobs.set(j);
  }

  setRecentJobs(j: Job[]): void {
    this.allRecentJobs.set(j);
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

    it('should treat "paused" as running', () => {
      component.setActiveJobs([
        createMockJob({ status: 'paused' as unknown as HelperJobStatus }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
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

    it('should not count processing, paused, or terminal jobs', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'paused' as unknown as HelperJobStatus }),
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

    it('should count paused toward the running numerator', () => {
      component.setActiveJobs([
        createMockJob({ status: 'paused' as unknown as HelperJobStatus }),
        createMockJob({ status: 'pending' }),
      ]);
      // paused → running (X), pending → pending (Y) → "1/2".
      expect(component.displayText()).toBe('1/2');
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

    it('should be false when there is at least one paused job', () => {
      component.setActiveJobs([
        createMockJob({ status: 'paused' as unknown as HelperJobStatus }),
      ]);
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

    it('should include paused jobs in the running subset', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'pa', status: 'paused' as unknown as HelperJobStatus }),
        createMockJob({ job_id: 'pr', status: 'processing' }),
        createMockJob({ job_id: 'pe', status: 'pending' }),
        createMockJob({ job_id: 'co', status: 'completed' }),
      ]);
      const ids = component.runningJobs().map((j) => j.job_id);
      expect(ids).toEqual(['pa', 'pr']);
    });
  });

  describe('tooltipText', () => {
    it('should produce "Running: X / Pending: Y" formatted tooltip', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'paused' as unknown as HelperJobStatus }),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
      ]);
      // 3 running (2 processing + 1 paused) and 3 pending → "Running: 3 / Pending: 3".
      expect(component.tooltipText()).toBe('Running: 3 / Pending: 3');
    });

    it('should produce "Running: 0 / Pending: 0" when idle', () => {
      component.setActiveJobs([]);
      expect(component.tooltipText()).toBe('Running: 0 / Pending: 0');
    });
  });

  describe('recentJobs (public computed)', () => {
    it('should filter out pending, processing, and paused jobs (terminal only via isTerminalStatus)', () => {
      component.setRecentJobs([
        createMockJob({ job_id: 'c1', status: 'completed' }),
        createMockJob({ job_id: 'p1', status: 'pending' }),
        createMockJob({ job_id: 'f1', status: 'failed' }),
        createMockJob({ job_id: 'r1', status: 'processing' }),
        createMockJob({ job_id: 'pa', status: 'paused' as unknown as HelperJobStatus }),
        createMockJob({ job_id: 'd1', status: 'dead_letter' }),
        createMockJob({ job_id: 'x1', status: 'cancelled' }),
      ]);
      const ids = component.recentJobs().map((j) => j.job_id);
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
      const ids = component.recentJobs().map((j) => j.job_id);
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
      expect(component.recentJobs().length).toBe(10);
    });

    it('should return an empty list when there are no terminal jobs', () => {
      component.setRecentJobs([
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'paused' as unknown as HelperJobStatus }),
      ]);
      expect(component.recentJobs()).toEqual([]);
    });
  });

  describe('onJobClick', () => {
    beforeEach(() => {
      // Seed a known project-name map so resolution is deterministic.
      const map = new Map<string | null, string>();
      map.set('project-abcdef', 'My Project');
      map.set('project-123', 'Project One Two Three');
      component.setProjectNameMap(map);
    });

    it('should close the menu before deciding tab/navigation', () => {
      const job = createMockJob({
        job_id: 'job-menu',
        project_id: 'project-abcdef',
        instance_id: 'inst-xyz',
        status: 'processing',
      });
      expect(component.menuClosedAfterClick).toBe(false);
      component.onJobClick(job);
      expect(component.menuClosedAfterClick).toBe(true);
    });

    it('should open project tab (resolved from projectNameMap) and navigate to instance with project_id + instance_id', () => {
      const job = createMockJob({
        job_id: 'job-1',
        project_id: 'project-abcdef',
        instance_id: 'inst-xyz',
        status: 'processing',
      });
      component.onJobClick(job);
      expect(component.lastTabAction).toEqual({
        kind: 'add',
        project_id: 'project-abcdef',
        name: 'My Project',
      });
      expect(component.lastNavigated).toEqual([
        '/projects',
        'project-abcdef',
        'instances',
        'inst-xyz',
      ]);
    });

    it('should fall back to first-8-chars of project_id when name is missing from projectNameMap', () => {
      const job = createMockJob({
        job_id: 'job-1b',
        project_id: 'project-unknown',
        instance_id: 'inst-xyz',
        status: 'processing',
      });
      component.onJobClick(job);
      expect(component.lastTabAction).toEqual({
        kind: 'add',
        project_id: 'project-unknown',
        name: 'project-',
      });
    });

    it('should setActiveTab("all") when project_id is null and navigate to specific instance', () => {
      const job = createMockJob({
        job_id: 'job-2',
        project_id: null,
        instance_id: 'inst-zzz',
        status: 'failed',
      });
      component.onJobClick(job);
      expect(component.lastTabAction).toEqual({
        kind: 'setActive',
        tabId: 'all',
      });
      expect(component.lastNavigated).toEqual([
        '/projects',
        'all',
        'instances',
        'inst-zzz',
      ]);
    });

    it('should navigate to the instances list (no null segment) when instance_id is null and project_id is set', () => {
      const job = createMockJob({
        job_id: 'job-3',
        project_id: 'project-123',
        instance_id: null,
        status: 'completed',
      });
      component.onJobClick(job);
      expect(component.lastNavigated).toEqual([
        '/projects',
        'project-123',
        'instances',
      ]);
      // No trailing null segment.
      expect(component.lastNavigated!.length).toBe(3);
      expect(component.lastNavigated!.every((s) => s !== null)).toBe(true);
      expect(component.lastTabAction).toEqual({
        kind: 'add',
        project_id: 'project-123',
        name: 'Project One Two Three',
      });
    });

    it('should navigate to /projects/all/instances (no null segment) when both project_id and instance_id are null', () => {
      const job = createMockJob({
        job_id: 'job-4',
        project_id: null,
        instance_id: null,
        status: 'completed',
      });
      component.onJobClick(job);
      expect(component.lastTabAction).toEqual({
        kind: 'setActive',
        tabId: 'all',
      });
      expect(component.lastNavigated).toEqual([
        '/projects',
        'all',
        'instances',
      ]);
      expect(component.lastNavigated!.length).toBe(3);
      expect(component.lastNavigated!.every((s) => s !== null)).toBe(true);
    });
  });

  describe('isRunningStatus', () => {
    it('should return true for "processing"', () => {
      expect(component.isRunningStatus('processing')).toBe(true);
    });

    it('should return true for "paused"', () => {
      expect(component.isRunningStatus('paused' as JobStatus)).toBe(true);
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
      expect(component.isPendingStatus('paused' as JobStatus)).toBe(false);
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
      expect(component.isTerminalStatus('paused' as JobStatus)).toBe(false);
      expect(component.isTerminalStatus('active' as JobStatus)).toBe(false);
      expect(component.isTerminalStatus('queued' as JobStatus)).toBe(false);
    });
  });
});
