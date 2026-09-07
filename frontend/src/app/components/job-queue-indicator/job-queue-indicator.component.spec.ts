import { signal, computed } from '@angular/core';
import { Job, JobStatus, MissionLiveness, isTerminalStatus, buildQueueTree } from '../../models/job.model';
import { MissionListResponse, MissionSummary, missionCountFromListResponse } from '../../models/mission.model';
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
  /**
   * Raw active jobs (running + paused + pending) — mirrors the
   * component's ``activeJobs`` signal. The mirror exposes it as a
   * readonly public signal so the panel-binding seam tests can assert
   * that the FULL non-terminal set (not the prior running-only filter)
   * reaches the embedded panel (C1 fix).
   */
  private readonly _activeJobs = signal<Job[]>([]);
  readonly activeJobs = this._activeJobs.asReadonly();

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
   *
   * The mirror exposes this signal as ``lastMissionsPayload`` (read-only)
   * so the C2 retention test can assert the raw payload wasn't
   * overwritten by a degraded-200 envelope.
   */
  private readonly _missionsPayload = signal<MissionListResponse | null>(null);
  readonly lastMissionsPayload = this._missionsPayload.asReadonly();

  /** Cached project_id → project name. */
  private readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  /** Wall-clock time of the last successful forkJoin — used by ``refreshAgeSeconds``. */
  private readonly _lastFetchAt = signal<number | null>(null);
  /** Readonly exposure so tests can pin the freeze semantics exactly. */
  readonly lastFetchAt = this._lastFetchAt.asReadonly();

  /**
   * C3 mirror — the latest non-null count from the canonical helper.
   * Initial ``null`` (pre-data); retained across degraded/null ticks
   * so the badge never flips back to a false bare 0/0.
   */
  private readonly liveMissionCountRaw = signal<number | null>(null);

  /**
   * C2/C3/W-jobs-intake mirror — non-null when the latest poll tick
   * had a per-leg failure; ``null`` on a clean tick. The UI surfaces
   * this as a degraded modifier on the missions segment so screen
   * readers don't read "0 live missions" during an outage.
   */
  readonly lastIntakeError = signal<string | null>(null);

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
    () => this._activeJobs().filter((j) => this.isRunningStatus(j.status)).length
  );

  pendingCount = computed(
    () => this._activeJobs().filter((j) => this.isPendingStatus(j.status)).length
  );

  totalNonTerminal = computed(() => this.runningCount() + this.pendingCount());

  isIdle = computed(() => this.totalNonTerminal() === 0);

  /**
   * C3 mirror — widened to ``number | null`` and routed through the
   * canonical ``missionCountFromListResponse`` helper. The mirror
   * feeds ``liveMissionCountRaw`` directly from ``applyFetchResult``
   * so the test pins below can drive the signal end-to-end without
   * re-implementing the helper.
   */
  liveMissionCount = computed<number | null>(() => this.liveMissionCountRaw());

  /** C3 mirror — handles null (pre-data state). */
  hasLiveMissions = computed(() => {
    const n = this.liveMissionCount();
    return n !== null && n > 0;
  });

  /** Defer-gate warning — mirrors the component's ``deferBlockWarning`` signal. */
  deferBlockWarning = signal<DeferBlockIndicator | null>(null);

  displayText = computed(() => {
    if (this.totalNonTerminal() === 0 && this.hasLiveMissions()) {
      return `missions: ${this.liveMissionCount() ?? 0}`;
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
    () => `${this.liveMissionCount() ?? 0}`
  );

  liveMissionBreakdown = computed(() => {
    const list = this.lastMissionsPayload()?.missions ?? [];
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
    return this.lastMissionsPayload()?.missions ?? [];
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
    const liveCount = this.liveMissionCount();
    const liveLine = liveCount === null
      ? 'Live missions: count unavailable'
      : `Live missions: ${liveCount} (processing ${breakdown.processing}, pending ${breakdown.pending}, paused ${breakdown.paused})`;
    return [
      `Running ${this.runningCount()} · Queued ${this.pendingCount()}`,
      liveLine,
      `refreshed ${this.refreshAgeSeconds()}s ago`,
    ].join('\n');
  });

  runningJobs = computed(() =>
    this._activeJobs().filter((j) => this.isRunningStatus(j.status))
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
    this._activeJobs.set(j);
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
   *
   * ``degraded`` defaults to ``false`` (the healthy tick shape). Tests
   * exercising C2 retention (a 200-OK ``degraded:true`` envelope) pass
   * ``{ degraded: true }`` to flip the flag — the prior hard-coded
   * ``false`` made the degraded-200 path unreachable from specs.
   */
  static buildMissionsPayload(
    overrides: Array<Partial<MissionSummary>>,
    total?: number,
    options?: { degraded?: boolean }
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
      total: options?.degraded ? null : (total ?? missions.length),
      limit: 20,
      offset: 0,
      has_more: false,
      degraded: options?.degraded ?? false,
    };
  }

  /**
   * Mirror of the real component's ``applyFetchResults`` — the
   * ``forkJoin`` next-handler body — driven with MOCKED service
   * payloads so tests prove the intake wiring without HTTP.
   *
   * Parity contract with the component (C2/C3/W-jobs-intake
   * honesty):
   * - ``active`` / ``recent`` may be ``null`` (per-leg catchError
   *   swallowed a failure); on ``null`` we RETAIN the previous list
   *   rather than resetting to ``[]``;
   * - ``missions === null`` (degraded count leg / 404-skew failure)
   *   RETAINS the previous payload — never falsely idle;
   * - C2 fix: a 200-OK ``degraded:true`` envelope ALSO retains the
   *   previous payload and DOES NOT touch the count signal;
   * - C3 fix: ``liveMissionCountRaw`` is updated only via the
   *   canonical ``missionCountFromListResponse`` helper, on a
   *   non-degraded tick;
   * - degraded-200 flag parity: a non-null ``degraded:true`` envelope
   *   ALSO raises ``lastIntakeError`` via ``onLegError`` (mirroring
   *   the component's ``recordLegError``) and counts as a FAILED leg
   *   for BOTH the clear gate and the ``lastFetchAt`` freeze gate —
   *   a degraded tick never clears a previously-set flag and never
   *   stamps a fresh "refreshed Ns ago" on an all-degraded tick;
   * - ``deferBlocked === null`` hides the warning; a payload is run
   *   through the canonical ``deferBlockIndicator`` helper;
   * - ``lastFetchAt`` advances only when at least one leg succeeded
   *   (missions: non-degraded).
   *
   * ``missions`` now carries the FULL ``MissionListResponse``
   * envelope (REPLACES the prior ``number | null`` signature) — the
   * segmented pill needs the per-liveness breakdown, not just a count.
   */
  applyFetchResult(
    active: Job[] | null,
    recent: Job[] | null,
    missions: MissionListResponse | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    if (active !== null) this._activeJobs.set(active);
    if (recent !== null) this.allRecentJobs.set(recent);
    if (missions !== null && !missions.degraded) {
      this._missionsPayload.set(missions);
      const count = missionCountFromListResponse(missions);
      this.liveMissionCountRaw.set(count);
    }
    const missionsDegraded = missions !== null && missions.degraded;
    if (missionsDegraded) this.onLegError('missions', 'degraded envelope');
    const anyNull =
      active === null ||
      recent === null ||
      missions === null ||
      deferBlocked === null ||
      missionsDegraded;
    if (!anyNull) this.lastIntakeError.set(null);
    if (
      active !== null ||
      recent !== null ||
      (missions !== null && !missions.degraded) ||
      deferBlocked !== null
    ) {
      this._lastFetchAt.set(Date.now());
    }
    this.deferBlockWarning.set(
      deferBlocked === null ? null : deferBlockIndicator(deferBlocked)
    );
  }

  // ---------------------------------------------------------------------------
  // Error-path mirror — per-leg catchError now isolates failures
  // (W-forkJoin legs); ``onLegError`` mirrors the component's
  // ``recordLegError`` so tests can pin the per-leg error flow.
  // ---------------------------------------------------------------------------

  /** Last error passed to ``onLegError`` — mirrors the ``console.warn`` side-effect. */
  lastLegError: { leg: string; err: unknown } | null = null;

  /** Count of per-leg errors recorded — for idempotency assertions. */
  legErrorCount = 0;

  /**
   * Mirror of the real component's ``recordLegError`` — sets
   * ``lastIntakeError`` so the UI can flip into a degraded visual.
   * Mirrors the W-jobs-intake-honesty contract: do NOT reset the
   * active/recent lists to ``[]`` (a bare 0/0 plus a stale
   * "refreshed Ns ago" would impersonate a healthy poll).
   */
  onLegError(leg: string, err: unknown): void {
    this.legErrorCount += 1;
    this.lastLegError = { leg, err };
    const reason =
      err instanceof Error ? err.message : typeof err === 'string' ? err : 'fetch failed';
    this.lastIntakeError.set(`${leg}: ${reason}`);
  }

  /**
   * Mirror of the real component's forkJoin safety-net error path —
   * kept as a last-resort handler for synchronous operator throws
   * that escape per-leg isolation. Per-leg catchError means this is
   * normally unreachable for routine HTTP failures.
   */
  onFetchError(err: unknown): void {
    this.legErrorCount += 1;
    this.lastLegError = { leg: 'forkjoin', err };
    const reason =
      err instanceof Error ? err.message : typeof err === 'string' ? err : 'fetch failed';
    this.lastIntakeError.set(`forkjoin: ${reason}`);
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

    it('C2/C3: 200-OK degraded envelope (degraded:true, total:null, missions:[]) is NOT written — last good data retained', () => {
      // Test pin 2 — the bug class the C2 fix closes: a degraded-200
      // response has degraded:true and empty rows + null total.
      // Before C2, the intake would write that envelope verbatim and
      // collapse the badge to "0/0" during an outage. After C2, the
      // canonical helper returns ``null`` and the indicator treats
      // the tick as a no-op against the last good payload.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [
            { mission_id: 'leader-a', liveness: 'processing' },
            { mission_id: 'leader-b', liveness: 'paused' },
          ],
          2
        ),
        null
      );
      expect(component.liveMissionCount()).toBe(2);
      expect(component.missionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');

      // Now the BE returns a degraded envelope — empty rows, null total,
      // degraded:true. The intake MUST NOT write this payload.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], undefined, { degraded: true }),
        null
      );
      // Last good data retained across the degraded tick.
      expect(component.liveMissionCount()).toBe(2);
      expect(component.missionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      // The raw payload signal was NOT overwritten either — the
      // canonical helper's null branch keeps the missions projection
      // honest. A subsequent healthy tick re-syncs both.
      const payload = component.lastMissionsPayload();
      expect(payload).not.toBeNull();
      expect(payload!.degraded).toBe(false);

      // The next healthy tick DOES update both signals.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], 0),
        null
      );
      expect(component.liveMissionCount()).toBe(0);
      expect(component.missionsList().length).toBe(0);
      expect(component.displayText()).toBe('0/0');
    });

    it('C3: degraded-200 envelope does NOT zero the count — canonical helper returns null and the signal retains last', () => {
      // Companion to the C2 test: pin the count leg independently.
      // Even if a future bug re-introduced the degraded-200 write,
      // the count helper routes through missionCountFromListResponse
      // and would refuse to zero the signal.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [{ mission_id: 'leader-a', liveness: 'processing' }],
          1
        ),
        null
      );
      // Direct helper sanity: degraded envelope returns null.
      const degradedEnvelope = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      expect(missionCountFromListResponse(degradedEnvelope)).toBeNull();
    });

    it('degraded-200 envelope RAISES lastIntakeError — a degraded-200 tick is a leg degradation (flag set + C2/C3 retention intact)', () => {
      // Re-verify finding: a 200-OK {degraded:true} envelope never
      // routes through catchError, so ``lastIntakeError`` stayed null
      // and the .degraded visual/aria modifier never flipped during a
      // degraded-200-only outage. The intake now treats the envelope
      // as a missions leg degradation.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload(
          [
            { mission_id: 'leader-a', liveness: 'processing' },
            { mission_id: 'leader-b', liveness: 'paused' },
          ],
          2
        ),
        null
      );
      expect(component.lastIntakeError()).toBeNull();

      // REAL degraded-200 envelope: missions=[], total=null, degraded:true.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], undefined, { degraded: true }),
        null
      );
      // The flag is SET — the degraded modifier + aria-label flip.
      expect(component.lastIntakeError()).toBe('missions: degraded envelope');
      // C2/C3 retention stays EXACTLY as-is across the same tick.
      expect(component.liveMissionCount()).toBe(2);
      expect(component.missionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      expect(component.lastMissionsPayload()!.degraded).toBe(false);
    });

    it('an all-degraded tick freezes lastFetchAt — degraded counts as a failed leg for the freshness stamp', () => {
      // The freeze gate folds degraded-missions in: a tick where every
      // leg failed OR degraded must NOT stamp a fresh timestamp ("N s
      // ago" would lie about staleness). A healthy tick still stamps.
      // Date.now is pinned because real-time ms resolution can make
      // both ticks land on the same millisecond (vacuous pass).
      const nowSpy = jest.spyOn(Date, 'now');
      try {
        nowSpy.mockReturnValue(1_000_000);
        component.applyFetchResult(
          [],
          [],
          MockJobQueueIndicatorComponent.buildMissionsPayload(
            [{ mission_id: 'leader-a', liveness: 'processing' }],
            1
          ),
          { defer_blocked: false, pending_count: 0, holders: [] }
        );
        const stampedAt = component.lastFetchAt();
        expect(stampedAt).toBe(1_000_000);

        // All-null legs + a degraded missions envelope: nothing usable
        // returned — the timestamp must stay byte-identical even as
        // the (controlled) wall clock advances.
        nowSpy.mockReturnValue(2_000_000);
        component.applyFetchResult(
          null,
          null,
          MockJobQueueIndicatorComponent.buildMissionsPayload([], undefined, { degraded: true }),
          null
        );
        expect(component.lastFetchAt()).toBe(stampedAt);
      } finally {
        nowSpy.mockRestore();
      }
    });

    it('shows pre-data state via null liveMissionCount + "0/0" displayText (no fake 0)', () => {
      // C3 fix: before any payload arrives the signal is ``null``,
      // not ``0`` — "count unavailable" must NOT be invented as 0.
      // The displayText fallback uses ``?? 0`` so the badge still
      // shows "0/0" without lying about whether a count has landed.
      expect(component.liveMissionCount()).toBeNull();
      expect(component.displayText()).toBe('0/0');
    });

    it('returns to "0/0" once a healthy tick reports zero live missions (legitimate update, not a degraded gap)', () => {
      // After a healthy tick with total=0 the signal is ``0`` (a real
      // value, not null). The display still reads "0/0" / "idle".
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], 0),
        null
      );
      expect(component.liveMissionCount()).toBe(0);
      expect(component.hasLiveMissions()).toBe(false);
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

  describe('runningJobs (computed subset — NOT the panel binding)', () => {
    // C1 fix: the panel input is now ``activeJobs()`` (full
    // non-terminal set). The internal ``runningJobs`` computed is
    // still useful for the badge's running count + tooltip, but it
    // MUST NOT be what flows to the panel. The seam test (below)
    // pins the binding change.

    it('runningJobs includes only processing/active jobs (badge helper)', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'r1', status: 'processing' }),
        createMockJob({ job_id: 'p1', status: 'pending' }),
        createMockJobWithStatus('active', { job_id: 'r2' }),
        createMockJob({ job_id: 'c1', status: 'completed' }),
      ]);
      const ids = component.runningJobs().map((j) => j.job_id);
      expect(ids).toEqual(['r1', 'r2']);
    });

    it('runningJobs includes paused jobs in the badge helper', () => {
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

  describe('activeJobs signal — the panel binding seam (C1)', () => {
    // C1 fix: the embedded panel's ``[activeJobs]`` binding reads the
    // FULL non-terminal set, NOT the prior ``runningJobs`` filter.
    // The old behaviour starved ``tree().queued`` of pending/queued
    // jobs — the badge said "2 queued" while the panel showed
    // nothing. This block pins the seam so the regression cannot
    // recur.

    it('activeJobs signal is public (read by the panel input)', () => {
      // Compile-time guard: ``activeJobs`` is the binding surface,
      // not the ``runningJobs`` helper. If someone renames the
      // signal back to private, this test will not catch it — but
      // the type system will (the template binding stops compiling).
      expect(typeof component.activeJobs).toBe('function');
    });

    it('passes the FULL non-terminal set (running + pending/queued) to the panel', () => {
      // The badge's runningCount is 2 (2 processing) and pendingCount
      // is 2 (1 pending + 1 queued). The OLD runningJobs filter
      // returned just the 2 running jobs; the panel's QUEUED section
      // silently starved.
      component.setActiveJobs([
        createMockJob({ job_id: 'r1', status: 'processing' }),
        createMockJob({ job_id: 'r2', status: 'processing' }),
        createMockJob({ job_id: 'p1', status: 'pending' }),
        createMockJobWithStatus('queued', { job_id: 'q1' }),
      ]);
      // activeJobs is the FULL non-terminal set — what flows to the
      // panel via [activeJobs]="activeJobs()".
      const ids = component.activeJobs().map((j) => j.job_id).sort();
      expect(ids).toEqual(['p1', 'q1', 'r1', 'r2']);
      // The legacy runningJobs filter is still narrower (only
      // processing/paused/active) — used for the badge helper.
      const runningIds = component.runningJobs().map((j) => j.job_id).sort();
      expect(runningIds).toEqual(['r1', 'r2']);
    });

    it('a pending/queued job in activeJobs reaches the panel’s QUEUED section via buildQueueTree', () => {
      // End-to-end seam pin: the panel receives activeJobs() and
      // routes unattached non-terminal jobs into ``tree().queued``.
      // A pending/queued job with no mission_id MUST land in
      // ``tree().queued`` — the prior running-only filter hid it.
      component.setActiveJobs([
        createMockJob({ job_id: 'q-orphan', status: 'pending', mission_id: null }),
      ]);
      // Mirror the panel's buildQueueTree call so the seam is
      // verifiable without an Angular TestBed harness.
      const tree = buildQueueTree(component.activeJobs(), [], []);
      expect(tree.queued.map((j) => j.job_id)).toEqual(['q-orphan']);
    });

    // Template-source pin (no TestBed): the mirror tests above prove
    // the signal's CONTENT, but nothing stopped the TEMPLATE from
    // reverting to the old filtered binding — which compiles green
    // and silently re-starves tree().queued (C1's exact regression
    // class). Read the template HTML relative to this spec and pin
    // the binding seam verbatim (same pattern as the instance-list
    // template-contract block).
    describe('template binding seam (source-text pin)', () => {
      let templateHtml: string;

      beforeAll(() => {
        // Resolve relative to this spec file.
        const path = require('path');
        const fs = require('fs');
        const specDir = __dirname;
        const htmlPath = path.join(specDir, 'job-queue-indicator.component.html');
        templateHtml = fs.readFileSync(htmlPath, 'utf-8');
      });

      it('binds the panel to the FULL non-terminal set: [activeJobs]="activeJobs()"', () => {
        expect(templateHtml).toContain('[activeJobs]="activeJobs()"');
      });

      it('does NOT bind [runningJobs] — the old running-only filter must not return', () => {
        expect(templateHtml).not.toContain('[runningJobs]');
      });
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

    it('should produce "Running 0 · Queued 0" plus "count unavailable" when idle (pre-data)', () => {
      // C3 fix: in pre-data state liveMissionCount is ``null`` and
      // the tooltip's ``Live missions`` line reads ``count
      // unavailable`` so the operator can distinguish a real idle
      // from a transient refresh gap.
      component.setActiveJobs([]);
      const tt = component.tooltipText();
      expect(tt).toContain('Running 0 · Queued 0');
      expect(tt).toContain('Live missions: count unavailable');
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

  describe('error handling (W-forkJoin legs + W-jobs-intake honesty)', () => {
    it('onLegError sets lastIntakeError and records the leg (no reset of activeJobs)', () => {
      // 1. Seed the mirror with non-empty data so the error path has
      //    something to RETAIN (otherwise the assertion is vacuous).
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

      // 3. Simulate the per-leg catchError firing on the ``active`` leg.
      component.onLegError('active', new Error('backend down'));

      // 4. The active/recent lists MUST be retained — a bare 0/0
      //    plus a stale "refreshed Ns ago" would impersonate a
      //    successful idle poll (W-jobs-intake honesty).
      expect(component.displayText()).toBe('1/2');
      expect(component.runningJobs().length).toBe(1);
      expect(component.recentJobs().length).toBe(1);
      expect(component.isIdle()).toBe(false);

      // 5. The leg error is captured for UI degradation signals.
      expect(component.legErrorCount).toBe(1);
      expect(component.lastLegError?.leg).toBe('active');
      expect(component.lastIntakeError()).toContain('active: backend down');
    });

    it('onFetchError (safety net) sets lastIntakeError and does not reset', () => {
      // The forkJoin safety-net path mirrors the per-leg error flow
      // and uses the same W-jobs-intake honesty contract — do NOT
      // reset the job lists on operator-thrown failures either.
      component.setActiveJobs([createMockJob({ status: 'processing' })]);

      component.onFetchError(new Error('network reset'));

      expect(component.displayText()).toBe('1/1');
      expect(component.runningJobs().length).toBe(1);
      expect(component.legErrorCount).toBe(1);
      expect(component.lastLegError?.leg).toBe('forkjoin');
      expect(component.lastIntakeError()).toContain('forkjoin: network reset');
    });

    it('records each per-leg error across repeated failures (W-forkJoin legs)', () => {
      // Each leg failure must advance the counter and update the
      // recorded error so the next log line reflects the current
      // failure, not a stale one.
      component.setActiveJobs([createMockJob({ status: 'processing' })]);

      component.onLegError('active', new Error('first failure'));
      expect(component.legErrorCount).toBe(1);
      expect((component.lastLegError?.err as Error).message).toBe('first failure');

      component.onLegError('recent', new Error('second failure'));
      expect(component.legErrorCount).toBe(2);
      expect(component.lastLegError?.leg).toBe('recent');
      expect((component.lastLegError?.err as Error).message).toBe('second failure');
    });

    it('accepts non-Error throwables (strings, objects) the way recordLegError does', () => {
      // ``catchError`` can deliver any thrown value; the error
      // handler must not assume the error is an ``Error`` instance.
      component.onLegError('missions', 'string error');
      expect(component.lastIntakeError()).toContain('missions: string error');

      component.onLegError('deferBlocked', { code: 500, reason: 'server' });
      // Non-Error, non-string values fall back to ``fetch failed``;
      // the reason text is intentionally not the literal object toString
      // (the UI gets a stable, short label).
      expect(component.lastIntakeError()).toContain('deferBlocked: fetch failed');
    });

    it('clears lastIntakeError when a clean tick lands after failures', () => {
      // Partial-failure ticks keep the flag set; a fully-clean
      // tick clears it so the UI returns to the healthy visual.
      component.onLegError('active', new Error('transient'));
      expect(component.lastIntakeError()).not.toBeNull();

      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], 0),
        { defer_blocked: false, pending_count: 0, holders: [] }
      );
      expect(component.lastIntakeError()).toBeNull();
    });

    it('a previously-set lastIntakeError SURVIVES a degraded-200 tick (error leg → degraded-200 → flag still set)', () => {
      // Re-verify finding: a degraded-200 tick is non-null on every
      // leg, so the old clear gate fired and WIPED a previously-set
      // error flag. The degraded envelope must keep the flag set
      // (re-raised by the missions leg itself, last-error-wins —
      // same overwrite semantics as concurrent per-leg catchError).
      component.onLegError('active', new Error('backend down'));
      expect(component.lastIntakeError()).toBe('active: backend down');

      // Degraded-200 missions tick — every leg non-null, but the
      // envelope is degraded. The flag must NOT be cleared.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], undefined, { degraded: true }),
        { defer_blocked: false, pending_count: 0, holders: [] }
      );
      expect(component.lastIntakeError()).toBe('missions: degraded envelope');

      // Recovery contract unchanged: the next FULLY clean tick
      // (non-null + non-degraded everywhere) clears the flag.
      component.applyFetchResult(
        [],
        [],
        MockJobQueueIndicatorComponent.buildMissionsPayload([], 0),
        { defer_blocked: false, pending_count: 0, holders: [] }
      );
      expect(component.lastIntakeError()).toBeNull();
    });

    it('passes a null leg through applyFetchResult without resetting (W-jobs-intake honesty)', () => {
      // The forkJoin per-leg catchError turns each failure into
      // ``null``. The applyFetchResult handler MUST NOT reset the
      // job list to ``[]`` on null — that's the impersonation gap.
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);

      component.applyFetchResult(null, null, null, null);

      expect(component.displayText()).toBe('1/1');
      expect(component.runningJobs().length).toBe(1);
      expect(component.recentJobs().length).toBe(1);
    });

    it('RETAINS the missions count across a jobs-fetch error (no false bare 0/0)', () => {
      // A live leader mission is proven by the missions projection;
      // the jobs intake then errors. The badge must show the last
      // good "missions: N" — NEVER a false bare 0/0 idle.
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

      component.onLegError('active', new Error('backend down'));

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
