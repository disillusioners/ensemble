import { signal, computed } from '@angular/core';
import { Job, JobStatus, liveMissionIds } from '../../models/job.model';
import { createMockJob, createMockJobWithStatus } from '../../testing/job-test-helpers';

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

  /**
   * Fix C (§8.2) mirror — delegates to the exported ``liveMissionIds``
   * model helper so the operator-facing badge contract is proven
   * against the real derivation, not a copy of the predicate. The
   * mirror's only input surface is the data (active + recent jobs);
   * the derivation itself lives in the model and is exercised by
   * ``job.model.spec.ts``.
   */
  liveMissionIds = computed(() =>
    liveMissionIds([...this.activeJobs(), ...this.recentJobs()])
  );

  liveMissionCount = computed(() => this.liveMissionIds().size);

  hasLiveMissions = computed(() => this.liveMissionCount() > 0);

  displayText = computed(() => {
    if (this.totalNonTerminal() === 0 && this.hasLiveMissions()) {
      return `missions: ${this.liveMissionCount()}`;
    }
    return `${this.runningCount()}/${this.totalNonTerminal()}`;
  });

  /**
   * Mirror of the real component's tooltip text computed.
   * Format: ``Running: X / Pending: Y`` plus a live-missions line
   * whenever the receipt window proves a parent mission is working.
   */
  tooltipText = computed(() => {
    const base = `Running: ${this.runningCount()} / Pending: ${this.pendingCount()}`;
    if (!this.hasLiveMissions()) {
      return base;
    }
    const plural = this.liveMissionCount() === 1 ? '' : 's';
    return (
      `${base} · Live missions: ${this.liveMissionCount()} ` +
      `(message${plural === '' ? '' : 's'} handled; parent mission${plural} still working)`
    );
  });

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

  // ---------------------------------------------------------------------------
  // Error-path mirror — replicates ``fetchJobs()``'s forkJoin error handler.
  //
  // C3 fix: ``JobService.listActiveJobs()`` and ``listRecentJobs()`` no longer
  // swallow failures, so errors propagate to a single ``forkJoin`` error
  // callback here that resets both ``activeJobs`` and ``allRecentJobs`` to
  // ``[]`` and logs via ``console.error``. The mirror records the last error
  // so tests can assert it was received without a real ``console.error``.
  // ---------------------------------------------------------------------------

  /** Last error passed to ``onFetchError`` — mirrors the ``console.error`` side-effect. */
  lastFetchError: unknown = null;

  /** Count of times ``onFetchError`` has been invoked — for idempotency assertions. */
  fetchErrorCount = 0;

  /**
   * Mirror of the real component's ``fetchJobs()`` error handler:
   * clears both raw signals to ``[]`` so the indicator surfaces "0/0"
   * (not a stale snapshot) until the next successful poll tick.
   */
  onFetchError(err: unknown): void {
    this.fetchErrorCount += 1;
    this.lastFetchError = err;
    this.activeJobs.set([]);
    this.allRecentJobs.set([]);
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
        createMockJobWithStatus('active'),
        createMockJob({ status: 'processing' }),
      ]);
      expect(component.runningCount()).toBe(2);
    });

    it('should treat "paused" as running', () => {
      component.setActiveJobs([
        createMockJobWithStatus('paused'),
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
        createMockJobWithStatus('queued'),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.pendingCount()).toBe(2);
    });

    it('should not count processing, paused, or terminal jobs', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJobWithStatus('paused'),
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
        createMockJobWithStatus('paused'),
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
        createMockJobWithStatus('paused'),
      ]);
      expect(component.isIdle()).toBe(false);
    });
  });

  // ── Fix C (§8.2) — badge mission awareness ───────────────────────────

  describe('liveMissionCount (Fix C receipt-derived missions)', () => {
    it('CASE A — 0 jobs + live-mission receipts: badge shows "missions: N" instead of bare 0/0', () => {
      // The 28c6421b read: leader visibly working, only terminal
      // receipts in the window, intake queue empty.
      component.setActiveJobs([]);
      component.setRecentJobs([
        createMockJob({
          job_id: 'm1', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
        }),
        createMockJob({
          job_id: 'm2', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: 'leader-b', job_type: 'message', mission_liveness: 'paused',
        }),
      ]);
      expect(component.liveMissionCount()).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      expect(component.isIdle()).toBe(true); // intake count is still 0 — display is what changes
    });

    it('CASE B — 0 jobs + 0 missions: badge reads bare "0/0" idle', () => {
      component.setActiveJobs([]);
      component.setRecentJobs([
        // Settled receipt: handled AND mission finished — must NOT count.
        createMockJob({
          job_id: 'm1', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: 'done-leader', job_type: 'message', mission_liveness: 'completed',
        }),
        // Degraded None: renders nothing, must NOT count.
        createMockJob({
          job_id: 'm2', status: 'failed', completed_at: new Date().toISOString(),
          instance_id: 'gone-leader', job_type: 'message', mission_liveness: null,
        }),
        // Mission row: no liveness by design, must NOT count.
        createMockJob({
          job_id: 't1', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: 'task-row', job_type: 'task', mission_liveness: null,
        }),
      ]);
      expect(component.liveMissionCount()).toBe(0);
      expect(component.displayText()).toBe('0/0');
      expect(component.tooltipText()).toBe('Running: 0 / Pending: 0');
    });

    it('CASE C — jobs present + live missions: X/Y display unchanged, tooltip explains both numbers', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
      ]);
      component.setRecentJobs([
        createMockJob({
          job_id: 'm1', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
        }),
      ]);
      expect(component.displayText()).toBe('1/1'); // intake count keeps primary billing
      expect(component.liveMissionCount()).toBe(1);
      expect(component.tooltipText()).toContain('Running: 1 / Pending: 0');
      expect(component.tooltipText()).toContain('Live missions: 1');
    });

    it('should de-duplicate multiple receipts from the same mission into one mission', () => {
      component.setActiveJobs([]);
      component.setRecentJobs([
        createMockJob({
          job_id: 'm1', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
        }),
        createMockJob({
          job_id: 'm2', status: 'failed', completed_at: new Date().toISOString(),
          instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
        }),
        createMockJob({
          job_id: 'm3', status: 'cancelled', completed_at: new Date().toISOString(),
          instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
        }),
      ]);
      // Three receipts, ONE live mission behind them.
      expect(component.liveMissionCount()).toBe(1);
      expect(component.displayText()).toBe('missions: 1');
    });

    it('should count live missions found in the ACTIVE list too (defensive mirror scan)', () => {
      component.setActiveJobs([
        createMockJob({
          job_id: 'a1', status: 'processing',
          instance_id: 'leader-active', job_type: 'message', mission_liveness: 'processing',
        }),
      ]);
      component.setRecentJobs([]);
      expect(component.liveMissionCount()).toBe(1);
    });

    it('should ignore mirror rows whose liveness is a settled value even at volume', () => {
      component.setActiveJobs([]);
      component.setRecentJobs(
        ['completed', 'failed', 'cancelled'].map((lv, i) =>
          createMockJob({
            job_id: `m${i}`, status: 'completed', completed_at: new Date().toISOString(),
            instance_id: `leader-${i}`, job_type: 'message',
            mission_liveness: lv as 'completed' | 'failed' | 'cancelled',
          })
        )
      );
      expect(component.liveMissionCount()).toBe(0);
    });

    it('should fall back to job_id when a live receipt carries no instance_id', () => {
      component.setActiveJobs([]);
      component.setRecentJobs([
        createMockJob({
          job_id: 'orphan-receipt', status: 'completed', completed_at: new Date().toISOString(),
          instance_id: null, job_type: 'message', mission_liveness: 'processing',
        }),
      ]);
      // Counts rather than silently vanishing.
      expect(component.liveMissionCount()).toBe(1);
    });
  });

  describe('runningJobs (computed subset for panel)', () => {
    it('should include only processing/active jobs', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'r1', status: 'processing' }),
        createMockJob({ job_id: 'p1', status: 'pending' }),
        createMockJobWithStatus('active', { job_id: 'r2' }),
        createMockJob({ job_id: 'c1', status: 'completed' }),
      ]);
      const ids = component.runningJobs().map((j) => j.job_id);
      expect(ids).toEqual(['r1', 'r2']);
    });

    it('should include paused jobs in the running subset', () => {
      component.setActiveJobs([
        createMockJobWithStatus('paused', { job_id: 'pa' }),
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
        createMockJobWithStatus('paused'),
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
        createMockJobWithStatus('paused', { job_id: 'pa' }),
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
        createMockJobWithStatus('paused'),
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

  describe('error handling (C3 propagation)', () => {
    it('should reset activeJobs and allRecentJobs to empty on fetch error', () => {
      // 1. Seed the mirror with non-empty data so the error path has
      //    something to clear (otherwise an empty starting state makes
      //    the assertion vacuous).
      component.setActiveJobs([
        createMockJob({ job_id: 'a1', status: 'processing' }),
        createMockJob({ job_id: 'a2', status: 'pending' }),
      ]);
      component.setRecentJobs([createMockJob({ job_id: 'r1', status: 'completed' })]);

      // 2. Pre-condition: data is present and the indicator reflects it.
      expect(component.runningCount()).toBe(1);
      expect(component.pendingCount()).toBe(1);
      expect(component.displayText()).toBe('1/2');
      expect(component.recentJobs().length).toBe(1);

      // 3. Simulate the forkJoin error handler firing on the mirror.
      component.onFetchError(new Error('backend down'));

      // 4. Both raw signals must be reset so the next poll starts clean.
      //    Display text must collapse to "0/0" and the panel subsets must
      //    be empty — this is the user-visible contract of the C3 fix.
      expect(component.displayText()).toBe('0/0');
      expect(component.runningJobs().length).toBe(0);
      expect(component.recentJobs().length).toBe(0);
      expect(component.isIdle()).toBe(true);

      // 5. The error is captured for logging parity with
      //    ``console.error('[JobQueueIndicator] Failed to fetch jobs:', err)``.
      expect(component.fetchErrorCount).toBe(1);
      expect(component.lastFetchError).toBeInstanceOf(Error);
      expect((component.lastFetchError as Error).message).toBe('backend down');
    });

    it('should stay empty when onFetchError fires on an already-empty mirror', () => {
      // Defensive: an empty starting state must remain empty — no throw,
      // no spurious data, and displayText stays "0/0".
      expect(component.displayText()).toBe('0/0');
      expect(component.isIdle()).toBe(true);

      component.onFetchError(new Error('network reset'));

      expect(component.displayText()).toBe('0/0');
      expect(component.runningJobs().length).toBe(0);
      expect(component.recentJobs().length).toBe(0);
      expect(component.fetchErrorCount).toBe(1);
    });

    it('should record each error and stay reset across repeated fetch failures', () => {
      // Repeated failures must not leave partial state behind and must
      // overwrite the recorded error so the next log line reflects the
      // current failure, not a stale one.
      component.setActiveJobs([
        createMockJob({ job_id: 'a1', status: 'processing' }),
      ]);
      component.setRecentJobs([createMockJob({ job_id: 'r1', status: 'failed' })]);

      component.onFetchError(new Error('first failure'));
      expect(component.displayText()).toBe('0/0');
      expect(component.fetchErrorCount).toBe(1);
      expect((component.lastFetchError as Error).message).toBe('first failure');

      // Re-seed and fail again — error counter advances, recorded error updates.
      component.setActiveJobs([
        createMockJob({ job_id: 'a1', status: 'processing' }),
      ]);
      component.onFetchError(new Error('second failure'));

      expect(component.displayText()).toBe('0/0');
      expect(component.fetchErrorCount).toBe(2);
      expect((component.lastFetchError as Error).message).toBe('second failure');
    });

    it('should accept non-Error throwables (strings, objects) the way console.error does', () => {
      // ``forkJoin`` can deliver any thrown value; the error handler must
      // not assume the error is an ``Error`` instance.
      component.setActiveJobs([createMockJob({ status: 'processing' })]);

      component.onFetchError('string error');
      expect(component.displayText()).toBe('0/0');
      expect(component.lastFetchError).toBe('string error');

      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.onFetchError({ code: 500, reason: 'server' });
      expect(component.displayText()).toBe('0/0');
      expect(component.lastFetchError).toEqual({ code: 500, reason: 'server' });
    });
  });
});
