import { signal, computed } from '@angular/core';
import { Job, JobStatus, MissionLiveness, isTerminalStatus } from '../../models/job.model';
import { MissionListResponse, MissionSummary } from '../../models/mission.model';
import { DeferBlockedStatus, DeferBlockIndicator, deferBlockIndicator } from '../../models/defer-blocked.model';
import { createMockJob, createMockJobWithStatus } from '../../testing/job-test-helpers';
import { firstValueFrom, forkJoin, of, throwError, catchError } from 'rxjs';

/**
 * Logic-mirror of JobQueueIndicatorComponent.
 *
 * This project does NOT use Angular TestBed for component tests. Instead, we
 * replicate the component's signal/computed logic in a plain TS class and
 * test it directly — same pattern as job-detail-drawer.component.spec.ts.
 *
 * The mirror exposes the private helpers (isRunningStatus, isPendingStatus)
 * that the real component keeps module-private so the assertions below can
 * exercise them directly. Terminal classification (``isTerminalStatus``) is
 * DELEGATED to the canonical model helper — the real component imports it
 * too, so the spec proves the badge contract against the real derivation,
 * not a local copy (the deleted local copy predated M3 ``settled``).
 *
 * We also expose the ``runningJobs`` computed, the public ``recentJobs``
 * computed (defensive via the canonical ``isTerminalStatus``), the
 * ``tooltipText`` computed, and captured ``onJobClick`` side-effects so
 * tests can assert navigation + tab-action decisions without instantiating
 * Angular.
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

  /**
   * Raw missions-list payload — mirrors the private ``missionsPayload``
   * signal. ``null`` = count unavailable (degraded leg / fetch failure);
   * the last known payload is RETAINED so the badge never falsely reads idle.
   *
   * REPLACES the former ``missionCountRaw: number | null`` signal — the
   * segmented pill needs the per-liveness breakdown so we carry the full
   * page here, not just a count.
   */
  private readonly missionsPayload = signal<MissionListResponse | null>(null);

  /** Cached project_id → project name. */
  private readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  /** Wall-clock time of the last successful forkJoin — used by ``refreshAgeSeconds``. */
  private readonly lastFetchAt = signal<number | null>(null);

  // ---------------------------------------------------------------------------
  // Helpers exposed as methods so tests can call them directly.
  // ---------------------------------------------------------------------------

  isRunningStatus(s: JobStatus): boolean {
    return s === 'processing' || s === 'paused' || (s as string) === 'active';
  }

  isPendingStatus(s: JobStatus): boolean {
    return s === 'pending' || (s as string) === 'queued';
  }

  /**
   * Delegates to the CANONICAL ``isTerminalStatus`` (models/job.model.ts)
   * — mirrors the real component's import. The former mirror-local copy
   * (completed/failed/cancelled/dead_letter only) is gone: ``settled``
   * receipts are terminal and this delegation proves it.
   */
  isTerminalStatus(s: JobStatus): boolean {
    return isTerminalStatus(s);
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
   * Mission-awareness mirror — the N comes from the authoritative
   * missions projection (``GET /api/missions``), fed via
   * ``applyFetchResult`` with mocked service payloads. ``null`` count
   * is NOT rendered as 0: the last known value is retained so the
   * badge never shows a false bare 0/0 while live missions exist.
   *
   * REPLACES the former ``missionCountRaw ?? 0`` fallback — now reads
   * the count leg out of the cached ``MissionListResponse``: prefer
   * ``payload.total`` (authoritative filter-aware COUNT) and fall
   * back to ``payload.missions.length`` only when the count leg
   * degraded (``total === null``).
   */
  liveMissionCount = computed(() => {
    const p = this.missionsPayload();
    if (!p) return 0;
    return p.total ?? p.missions.length;
  });

  hasLiveMissions = computed(() => this.liveMissionCount() > 0);

  /** Defer-gate warning — mirrors the component's ``deferBlockWarning`` signal. */
  deferBlockWarning = signal<DeferBlockIndicator | null>(null);

  displayText = computed(() => {
    if (this.totalNonTerminal() === 0 && this.hasLiveMissions()) {
      return `missions: ${this.liveMissionCount()}`;
    }
    return `${this.runningCount()}/${this.totalNonTerminal()}`;
  });

  /**
   * Pill STATE — mirrors the real component. Three branches:
   *   * 'segmented' — jobs present (running OR pending)
   *   * 'missions-only' — queue idle, live missions exist
   *   * 'idle' — both empty
   */
  pillState = computed<'segmented' | 'missions-only' | 'idle'>(() => {
    if (this.totalNonTerminal() > 0) return 'segmented';
    if (this.hasLiveMissions()) return 'missions-only';
    return 'idle';
  });

  jobsSegmentText = computed(
    () => `${this.runningCount()}/${this.totalNonTerminal()}`
  );

  missionsSegmentText = computed(
    () => `${this.liveMissionCount()}`
  );

  liveMissionBreakdown = computed(() => {
    const list = this.missionsPayload()?.missions ?? [];
    let processing = 0;
    let pending = 0;
    let paused = 0;
    for (const m of list) {
      if (m.liveness === 'processing') processing += 1;
      else if (m.liveness === 'pending') pending += 1;
      else if (m.liveness === 'paused') paused += 1;
    }
    return { processing, pending, paused } as Record<string, number>;
  });

  missionsList = computed<MissionSummary[]>(() => {
    return this.missionsPayload()?.missions ?? [];
  });

  refreshAgeSeconds = computed(() => {
    if (!this.lastFetchAt()) return 0;
    return Math.max(0, Math.floor((Date.now() - this.lastFetchAt()!) / 1000));
  });

  /**
   * Tooltip text mirror. New multi-line breakdown:
   *   Running X · Queued Y
   *   Live missions: N (processing a, pending b, paused c)
   *   refreshed Ns ago
   */
  tooltipText = computed(() => {
    const breakdown = this.liveMissionBreakdown();
    return [
      `Running ${this.runningCount()} · Queued ${this.pendingCount()}`,
      `Live missions: ${this.liveMissionCount()} (processing ${breakdown.processing}, pending ${breakdown.pending}, paused ${breakdown.paused})`,
      `refreshed ${this.refreshAgeSeconds()}s ago`,
    ].join('\n');
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

  /**
   * Build a ``MissionListResponse`` payload from a total + an array of
   * per-mission overrides. The fake mirrors the BE wire shape so the
   * forkJoin leg can be exercised end-to-end without HTTP. ``total``
   * defaults to ``missions.length`` so the badge's
   * ``total ?? missions.length`` fallback stays consistent.
   */
  static buildMissionsPayload(
    overrides: Array<Partial<MissionSummary>>,
    total?: number
  ): MissionListResponse {
    const missions: MissionSummary[] = overrides.map((o, i) => ({
      mission_id: `m-${i}`,
      agent_id: 'leader',
      parent_mission_id: null,
      liveness: 'processing' as MissionLiveness,
      terminal_reason: null,
      epoch: 1,
      linked_jobs: [],
      started_at: '2026-09-07T10:00:00Z',
      last_activity_at: '2026-09-07T10:30:00Z',
      title: null,
      initiative_preview: null,
      ...o,
    }));
    return {
      missions,
      total: total ?? missions.length,
      limit: 20,
      offset: 0,
      has_more: false,
      degraded: false,
    };
  }

  /**
   * Mirror of the real component's ``applyFetchResults`` — the
   * ``forkJoin`` next-handler body — driven with MOCKED service
   * payloads so tests prove the intake wiring without HTTP.
   *
   * Parity contract with the component:
   * - jobs (active + recent) stored verbatim;
   * - ``missions === null`` (degraded count leg / 404-skew failure)
   *   RETAINS the previous payload — never falsely idle;
   * - ``deferBlocked === null`` hides the warning; a payload is run
   *   through the canonical ``deferBlockIndicator`` helper.
   *
   * ``missions`` now carries the FULL ``MissionListResponse``
   * envelope (REPLACES the prior ``number | null`` signature) — the
   * segmented pill needs the per-liveness breakdown, not just a count.
   */
  applyFetchResult(
    active: Job[],
    recent: Job[],
    missions: MissionListResponse | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    this.activeJobs.set(active);
    this.allRecentJobs.set(recent);
    if (missions !== null) {
      this.missionsPayload.set(missions);
    }
    this.lastFetchAt.set(Date.now());
    this.deferBlockWarning.set(
      deferBlocked === null ? null : deferBlockIndicator(deferBlocked)
    );
  }

  // ---------------------------------------------------------------------------
  // Error-path mirror — replicates ``fetchBadgeSignals()``'s forkJoin error
  // handler.
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
   * Mirror of the real component's ``fetchBadgeSignals()`` error handler:
   * clears both JOB signals to ``[]`` so the indicator surfaces the
   * intake truthfully (not a stale snapshot) until the next
   * successful poll tick. The missions count is deliberately NOT
   * cleared — "count unavailable" retains the last known value so a
   * live leader mission never flips the badge to a false bare 0/0.
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

  // ── Mission awareness — sourced from the authoritative projection ───

  describe('liveMissionCount (missions-API sourced — Change 1)', () => {
    it('CASE A — 0 jobs + missions projection reports 2: badge shows "missions: 2", never bare 0/0', () => {
      // The original 28c6421b read: leader visibly working, only terminal
      // receipts in the window, intake queue empty. The receipt-derived
      // N went stale (badge 0/0) — the authoritative /api/missions count
      // is now the source, so the settled receipts in the recent window
      // are irrelevant to the badge's N.
      component.applyFetchResult(
        [],
        [
          createMockJob({
            job_id: 'm1', status: 'settled', completed_at: new Date().toISOString(),
            instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
          }),
          createMockJob({
            job_id: 'm2', status: 'completed', completed_at: new Date().toISOString(),
            instance_id: 'leader-b', job_type: 'message', mission_liveness: 'completed',
          }),
        ],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [
            { mission_id: 'leader-a', liveness: 'processing' },
            { mission_id: 'leader-b', liveness: 'processing' },
          ],
          2
        ),
        null
      );
      expect(component.liveMissionCount()).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      expect(component.isIdle()).toBe(true); // intake count is still 0 — display is what changes
    });

    it('CASE B — missions projection reports 0: badge reads bare "0/0" idle', () => {
      component.applyFetchResult(
        [],
        [
          // Terminal receipt: handled AND mission finished — must NOT count.
          createMockJob({
            job_id: 'm1', status: 'settled', completed_at: new Date().toISOString(),
            instance_id: 'done-leader', job_type: 'message', mission_liveness: 'completed',
          }),
        ],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], 0),
        null
      );
      expect(component.liveMissionCount()).toBe(0);
      expect(component.displayText()).toBe('0/0');
      // Tooltip's first line explains the breakdown.
      expect(component.tooltipText()).toContain('Running 0 · Queued 0');
      expect(component.tooltipText()).toContain('Live missions: 0');
    });

    it('CASE C — jobs present + missions projection reports 1: X/Y display unchanged, tooltip explains both numbers', () => {
      component.applyFetchResult(
        [createMockJob({ status: 'processing' })],
        [
          createMockJob({
            job_id: 'm1', status: 'settled', completed_at: new Date().toISOString(),
            instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
          }),
        ],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [{ mission_id: 'leader-a', liveness: 'processing' }],
          1
        ),
        null
      );
      expect(component.displayText()).toBe('1/1'); // intake count keeps primary billing
      expect(component.liveMissionCount()).toBe(1);
      expect(component.tooltipText()).toContain('Running 1 · Queued 0');
      expect(component.tooltipText()).toContain('Live missions: 1');
    });

    it('retains the last known payload when the missions leg degrades to null — never a false bare 0/0', () => {
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [{ mission_id: 'leader-a', liveness: 'processing' }],
          2
        ),
        null
      );
      expect(component.displayText()).toBe('missions: 2');

      // Degraded count leg (total=null) or a failed fetch: "data
      // unavailable" must NOT collapse to 0 — the badge retains the
      // last good payload.
      component.applyFetchResult([], [], null, null);
      expect(component.liveMissionCount()).toBe(2);
      expect(component.displayText()).toBe('missions: 2');

      // ...and the next healthy tick corrects downward.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], 0),
        null
      );
      expect(component.displayText()).toBe('0/0');
    });

    it('shows bare 0/0 before any missions payload arrives (pre-data state)', () => {
      expect(component.liveMissionCount()).toBe(0);
      expect(component.displayText()).toBe('0/0');
    });
  });

  describe('defer-blocked warning application (Change 2 FE)', () => {
    const pausedHolder = {
      instance_id: 'abc-999',
      agent: 'leader',
      status: 'paused',
      // BE wire truth: ISO-8601 +00:00-normalized UTC (NOT a trailing Z).
      since: '2026-09-04T08:05:00+00:00',
      kind: 'paused' as const,
    };

    it('AMBER warning derived through the canonical helper when a paused holder blocks jobs', () => {
      component.applyFetchResult([], [], null, {
        defer_blocked: true,
        pending_count: 1,
        holders: [pausedHolder],
      });
      const warn = component.deferBlockWarning();
      expect(warn).not.toBeNull();
      expect(warn!.severity).toBe('amber');
      expect(warn!.tooltip).toBe(
        'held by paused instance abc-999 since 2026-09-04 08:05 UTC — resume or terminate to unblock'
      );
    });

    it('AMBER with a null since (all source columns NULL) renders "unknown time" through the intake wiring', () => {
      // P0 type-truth companion: the wire type is ``string | null``; the
      // component-level intake must surface the helper's null handling
      // verbatim (no undefined/NaN leaking into the tooltip).
      component.applyFetchResult([], [], null, {
        defer_blocked: true,
        pending_count: 1,
        holders: [{ ...pausedHolder, since: null }],
      });
      const warn = component.deferBlockWarning();
      expect(warn).not.toBeNull();
      expect(warn!.severity).toBe('amber');
      expect(warn!.tooltip).toContain('since unknown time — resume or terminate to unblock');
    });

    it('warning hidden when the endpoint degrades to null (404/503 rollout skew)', () => {
      component.applyFetchResult([], [], null, { defer_blocked: true, pending_count: 1, holders: [pausedHolder] });
      expect(component.deferBlockWarning()).not.toBeNull();

      component.applyFetchResult([], [], null, null);
      expect(component.deferBlockWarning()).toBeNull();
    });

    it('no render when pending_count is 0 — the gate lives in the helper', () => {
      component.applyFetchResult([], [], null, { defer_blocked: false, pending_count: 0, holders: [] });
      expect(component.deferBlockWarning()).toBeNull();
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
    it('should produce multi-line breakdown with "Running X · Queued Y" plus missions + refreshed lines', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
        createMockJobWithStatus('paused'),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
      ]);
      // 3 running (2 processing + 1 paused) and 3 pending → "Running 3 · Queued 3".
      const tt = component.tooltipText();
      expect(tt).toContain('Running 3 · Queued 3');
      expect(tt).toContain('Live missions:');
      expect(tt).toContain('refreshed');
    });

    it('should produce "Running 0 · Queued 0" when idle', () => {
      component.setActiveJobs([]);
      const tt = component.tooltipText();
      expect(tt).toContain('Running 0 · Queued 0');
      expect(tt).toContain('Live missions: 0');
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
        // M3 mirror-receipt terminal: ``settled`` must be treated as
        // terminal via the CANONICAL ``isTerminalStatus`` import (the
        // deleted module-local copy misclassified it as non-terminal).
        createMockJob({
          job_id: 's1',
          status: 'settled',
          job_type: 'message',
          mission_liveness: 'processing',
        }),
      ]);
      const ids = component.recentJobs().map((j) => j.job_id);
      // Equal-timestamp ties keep insertion order (stable sort); the
      // settled receipt lands in the terminal mix, not on the floor.
      expect(ids).toEqual(['c1', 'f1', 'd1', 'x1', 's1']);
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

  describe('isTerminalStatus (canonical import — drive-by #2)', () => {
    it('should return true for terminal states', () => {
      expect(component.isTerminalStatus('completed')).toBe(true);
      expect(component.isTerminalStatus('failed')).toBe(true);
      expect(component.isTerminalStatus('cancelled')).toBe(true);
      expect(component.isTerminalStatus('dead_letter')).toBe(true);
    });

    it('should treat "settled" (M3 mirror-receipt terminal) as terminal via the canonical helper', () => {
      // Regression proof for the deleted module-local copy: the stale
      // component-local predicate (completed/failed/cancelled/dead_letter
      // only) misclassified settled receipts as non-terminal. The
      // canonical models/job.model.ts helper includes settled.
      expect(component.isTerminalStatus('settled')).toBe(true);
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
      //    ``console.error('[JobQueueIndicator] Failed to fetch badge signals:', err)``.
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

    it('should RETAIN the missions count across a jobs-fetch error (no false bare 0/0)', () => {
      // A live leader mission is proven by the missions projection; the
      // jobs intake then errors (forkJoin-level failure). The badge may
      // show stale "missions: N" — NEVER a false bare 0/0 idle.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [{ mission_id: 'leader-a', liveness: 'processing' }],
          2
        ),
        null
      );
      expect(component.displayText()).toBe('missions: 2');

      component.onFetchError(new Error('backend down'));

      expect(component.liveMissionCount()).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
    });
  });

  describe('forkJoin participant isolation (round-1 wiring contract)', () => {
    it('a throwing additive participant cannot kill the survivors on the same tick', async () => {
      // Real RxJS, mirroring ``fetchBadgeSignals()`` 1:1: each additive
      // participant isolates its own error with ``catchError(() => of(null))``
      // so a missions/defer-blocked failure degrades to ``null`` while the
      // jobs intake still emits.
      const result = await firstValueFrom(forkJoin({
        active: of([createMockJob({ status: 'processing' })]),
        missions: throwError(() => new Error('missions 500')).pipe(catchError(() => of(null))),
        deferBlocked: throwError(() => new Error('defer-blocked 404')).pipe(catchError(() => of(null))),
      }));
      expect(result.active.length).toBe(1); // jobs intake survived both failures
      expect(result.missions).toBeNull();
      expect(result.deferBlocked).toBeNull();
    });
  });

  // ── Segmented status pill (2026-09-07, mission-tree panel) ──────────

  describe('segmented status pill — pillState branch', () => {
    it('returns "segmented" when jobs are present (regardless of missions)', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      expect(component.pillState()).toBe('segmented');
    });

    it('returns "missions-only" when no jobs but live missions exist', () => {
      component.setActiveJobs([]);
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [{ mission_id: 'm-1', liveness: 'processing' }],
          1
        ),
        null
      );
      expect(component.pillState()).toBe('missions-only');
    });

    it('returns "idle" when both jobs and missions are empty', () => {
      component.setActiveJobs([]);
      expect(component.pillState()).toBe('idle');
    });
  });

  describe('segmented pill — jobsSegmentText / missionsSegmentText', () => {
    it('jobsSegmentText: "X/Y" with running + total non-terminal', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.jobsSegmentText()).toBe('2/3');
    });

    it('missionsSegmentText: just the integer N', () => {
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [
            { mission_id: 'm-1', liveness: 'processing' },
            { mission_id: 'm-2', liveness: 'paused' },
          ],
          2
        ),
        null
      );
      expect(component.missionsSegmentText()).toBe('2');
    });
  });

  describe('segmented pill — liveMissionBreakdown (per-liveness counts)', () => {
    it('tallies processing / pending / paused from the missions list', () => {
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([
          { mission_id: 'm-1', liveness: 'processing' },
          { mission_id: 'm-2', liveness: 'processing' },
          { mission_id: 'm-3', liveness: 'paused' },
          { mission_id: 'm-4', liveness: 'pending' },
        ]),
        null
      );
      const bd = component.liveMissionBreakdown();
      expect(bd.processing).toBe(2);
      expect(bd.paused).toBe(1);
      expect(bd.pending).toBe(1);
    });

    it('zeros when no missions payload is present', () => {
      const bd = component.liveMissionBreakdown();
      expect(bd.processing).toBe(0);
      expect(bd.paused).toBe(0);
      expect(bd.pending).toBe(0);
    });
  });

  describe('missionsList signal — feeds the panel', () => {
    it('exposes the latest successful missions payload', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
        { mission_id: 'm-2', liveness: 'paused' },
      ]);
      component.applyFetchResult([], [], payload, null);
      expect(component.missionsList().length).toBe(2);
      expect(component.missionsList().map((m) => m.mission_id).sort()).toEqual(['m-1', 'm-2']);
    });

    it('retains the last known missions across a degraded poll', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
      ]);
      component.applyFetchResult([], [], payload, null);
      component.applyFetchResult([], [], null, null);
      expect(component.missionsList().length).toBe(1);
      expect(component.missionsList()[0].mission_id).toBe('m-1');
    });
  });
});
