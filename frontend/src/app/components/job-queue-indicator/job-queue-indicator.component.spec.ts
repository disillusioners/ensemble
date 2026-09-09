import { signal, computed } from '@angular/core';
import { Job, JobStatus, MissionLiveness, isTerminalStatus } from '../../models/job.model';
import { buildInstanceNodes, buildInstanceTree, InstanceRow, InstanceNode } from '../../models/instance-node.model';
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
   * F-5 mirror (2026-09-08, updated for the instances-primary tree) —
   * ``_liveMissionsPayload`` mirrors LEG A
   * (``listMissions({ liveness: 'processing,pending,paused',
   * limit: 20 })``): the single source of truth for the badge count +
   * tooltip per-liveness breakdown. Mirrors the real component's
   * ``liveMissionsPayload``.
   *
   * The former LEG B (unfiltered ``listMissions({ limit: 20 })``)
   * is DROPPED — recent terminal mission nodes are replaced by
   * terminal roots from the instances page (``_instancesPayload``
   * below).
   */
  private readonly _liveMissionsPayload = signal<MissionListResponse | null>(null);
  readonly lastLiveMissionsPayload = this._liveMissionsPayload.asReadonly();

  /**
   * Instances-primary tree leg mirror (2026-09-08, design V1) — the
   * FLAT instance rows from the last successful
   * ``listInstanceTree(10)`` call. Mirrors the real component's
   * ``instancesPayload`` signal (retention: ``null`` leg ⇒ untouched).
   * Read-only exposure ``lastInstancesPayload`` so the F-5-class
   * pins can assert the tree payload end-to-end.
   */
  private readonly _instancesPayload = signal<InstanceRow[]>([]);
  readonly lastInstancesPayload = this._instancesPayload.asReadonly();

  /**
   * Derived nested roots — mirrors the real ``instanceRoots``
   * computed (delegates to the REAL ``buildInstanceNodes`` helper,
   * as the real component does).
   */
  readonly instanceRoots = computed<InstanceNode[]>(() =>
    buildInstanceNodes(this._instancesPayload())
  );

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

  /**
   * F-5 mirror (2026-09-08) — breakdown reads from LEG A only. The
   * real component derives its breakdown from ``liveMissionsList``
   * (LEG A), never from the panel's combined input, so the tooltip's
   * per-liveness counts always match what the badge counts. Mirrors
   * the real component's ``liveMissionBreakdown``.
   */
  liveMissionBreakdown = computed(() => {
    const list = this.lastLiveMissionsPayload()?.missions ?? [];
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

  /**
   * F-5 mirror — LEG A's mission rows verbatim. Mirrors the real
   * component's ``liveMissionsList``. Read-only exposure so the F-5
   * pins can assert the live-only rows without going through the
   * panel composition.
   */
  liveMissionsList = computed<MissionSummary[]>(() => {
    return this.lastLiveMissionsPayload()?.missions ?? [];
  });

  /**
   * Instances-primary tree (2026-09-08, design V1) — the former
   * ``missionsList`` LEG A + LEG B composition is GONE (leg B
   * dropped, panel's ``[missions]`` input removed). The panel's tree
   * input is ``[instances]="instanceRoots()"``.
   */
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

  /**
   * T1 mirror — last navigation decided by ``onFooterClick``. Captured
   * separately from ``lastNavigated`` so the spec can assert that the
   * footer activation routes to ``/jobs`` (the Jobs page route
   * confirmed in app.routes.ts) WITHOUT conflating it with the
   * ``onJobClick`` navigation surface. ``menuClosedAfterClick`` is
   * also flipped so the mirror's "menu close before navigation"
   * contract stays identical for both entry points.
   */
  lastFooterNavigated: (string | null)[] | null = null;

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

  /**
   * T1 mirror — ``onFooterClick`` closes the menu and routes to the
   * dedicated Jobs page (confirmed lazy route at ``/jobs`` in
   * app.routes.ts). Mirrors the real component bit-for-bit.
   */
  onFooterClick(): void {
    this.menuClosedAfterClick = true;
    this.lastFooterNavigated = ['/jobs'];
  }

  /**
   * Instances-primary tree mirror (2026-09-08, design V1) —
   * ``onInstanceClick`` captures: menu close FIRST, then the tab
   * decision (project tab add via projectNameMap with first-8-chars
   * fallback, or setActiveTab('all') on a null project — the same
   * null-project fallback as ``onJobClick``), then the route to
   * ``/projects/<key>/instances/<instance_id>``.
   */
  lastInstanceNavigated: (string | null)[] | null = null;

  onInstanceClick(node: InstanceNode): void {
    this.menuClosedAfterClick = true;
    const projectKey = node.instance.project_id || 'all';
    if (node.instance.project_id) {
      const name =
        this.projectNameMap().get(node.instance.project_id) ??
        node.instance.project_id.slice(0, 8);
      this.lastTabAction = { kind: 'add', project_id: node.instance.project_id, name };
    } else {
      this.lastTabAction = { kind: 'setActive', tabId: 'all' };
    }
    this.lastInstanceNavigated = [
      '/projects',
      projectKey,
      'instances',
      node.instance.instance_id,
    ];
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
   * Build a flat instance-rows page (the ``listInstanceTree`` leg's
   * emission) from per-row overrides — mirrors the ``GET
   * /api/instances`` wire shape (flat rows, descendants included).
   */
  static buildInstanceRows(
    overrides: Array<Partial<InstanceRow>>
  ): InstanceRow[] {
    return overrides.map((o, i) => ({
      instance_id: `i-${i}`,
      agent_id: 'leader',
      agent_tag: null,
      status: 'running' as const,
      parent_id: null,
      title: null,
      initiative_message: null,
      children: [],
      created_at: '2026-09-08T09:00:00Z',
      updated_at: '2026-09-08T10:00:00Z',
      project_id: 'p-1',
      pinned: false,
      color_tag: null,
      icon_tag: null,
      pinned_at: null,
      ...o,
    }));
  }

  /** Setter mirroring the writable private signal (test drive). */
  setInstancesPayload(rows: InstanceRow[]): void {
    this._instancesPayload.set(rows);
  }

  /**
   * Mirror of the real component's ``applyFetchResults`` — the
   * ``forkJoin`` next-handler body — driven with MOCKED service
   * payloads so tests prove the intake wiring without HTTP.
   *
   * Parity contract with the component (C2/C3/W-jobs-intake
   * honesty + instances-primary tree leg):
   * - ``active`` / ``recent`` may be ``null`` (per-leg catchError
   *   swallowed a failure); on ``null`` we RETAIN the previous list
   *   rather than resetting to ``[]``;
   * - ``liveMissions === null`` (degraded live leg / 404-skew
   *   failure) RETAINS the previous live count + payload — never
   *   falsely idle; the tooltip breakdown stays sourced from LEG A;
   * - ``instances === null`` (per-leg failure) RETAINS the previous
   *   tree payload — the panel never flashes empty;
   * - ``instances`` non-null ⇒ the payload write lands (mirrors the
   *   production ``instancesPayload.set(instances)`` write, whose
   *   exact text is source-pinned — the F-5 lesson class);
   * - C2 fix: a 200-OK ``degraded:true`` envelope on the missions
   *   leg retains the previous payload and DOES NOT touch the
   *   signal;
   * - C3 fix: ``liveMissionCountRaw`` is updated only via the
   *   canonical ``missionCountFromListResponse`` helper, on a
   *   non-degraded LEG A tick;
   * - degraded-200 flag parity: a non-null ``degraded:true`` envelope
   *   on the missions leg ALSO raises ``lastIntakeError`` via
   *   ``onLegError`` (mirroring the component's ``recordLegError``)
   *   and counts as a FAILED leg for BOTH the clear gate and the
   *   ``lastFetchAt`` freeze gate;
   * - ``deferBlocked === null`` hides the warning; a payload is run
   *   through the canonical ``deferBlockIndicator`` helper;
   * - ``lastFetchAt`` advances only when at least one leg succeeded
   *   (missions leg: non-degraded).
   *
   * The former LEG B (``recentMissions``) is DROPPED — its content
   * leg was replaced by the instances page (design V1).
   */
  applyFetchResult(
    active: Job[] | null,
    recent: Job[] | null,
    liveMissions: MissionListResponse | null,
    instances: InstanceRow[] | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    if (active !== null) this._activeJobs.set(active);
    if (recent !== null) this.allRecentJobs.set(recent);
    if (liveMissions !== null && !liveMissions.degraded) {
      const count = missionCountFromListResponse(liveMissions);
      this.liveMissionCountRaw.set(count);
      // LEG A payload write — tooltip breakdown source. Mirrors the
      // production write at job-queue-indicator.component.ts inside
      // ``applyFetchResults`` (the F-5 source-drift pin guards the
      // prod counterpart).
      this._liveMissionsPayload.set(liveMissions);
    }
    // Instances-primary tree leg — the F-5-pinned production write.
    // ``null`` (per-leg failure) retains the last good tree.
    if (instances !== null) {
      this._instancesPayload.set(instances);
    }
    const liveMissionsDegraded = liveMissions !== null && liveMissions.degraded;
    if (liveMissionsDegraded) this.onLegError('liveMissions', 'degraded envelope');
    const anyNull =
      active === null ||
      recent === null ||
      liveMissions === null ||
      instances === null ||
      deferBlocked === null ||
      liveMissionsDegraded;
    if (!anyNull) this.lastIntakeError.set(null);
    if (
      active !== null ||
      recent !== null ||
      (liveMissions !== null && !liveMissions.degraded) ||
      instances !== null ||
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
      const emptyPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult(
        [],
        [
          // Terminal receipt: handled AND mission finished — must NOT count.
          createMockJob({
            job_id: 'm1', status: 'settled', completed_at: new Date().toISOString(),
            instance_id: 'done-leader', job_type: 'message', mission_liveness: 'completed',
          }),
        ],
        emptyPayload,
        emptyPayload,
        null
      );
      expect(component.liveMissionCount()).toBe(0);
      expect(component.displayText()).toBe('0/0');
      // Tooltip's first line explains the breakdown.
      expect(component.tooltipText()).toContain('Running 0 · Queued 0');
      expect(component.tooltipText()).toContain('Live missions: 0');
    });

    it('CASE C — jobs present + missions projection reports 1: X/Y display unchanged, tooltip explains both numbers', () => {
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'leader-a', liveness: 'processing' }],
        1
      );
      component.applyFetchResult(
        [createMockJob({ status: 'processing' })],
        [
          createMockJob({
            job_id: 'm1', status: 'settled', completed_at: new Date().toISOString(),
            instance_id: 'leader-a', job_type: 'message', mission_liveness: 'processing',
          }),
        ],
        onePayload,
        onePayload,
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
      component.applyFetchResult([], [], null, null, null);
      expect(component.liveMissionCount()).toBe(2);
      expect(component.displayText()).toBe('missions: 2');

      // ...and the next healthy tick corrects downward.
      const zeroPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult([], [], zeroPayload, zeroPayload, null);
      expect(component.displayText()).toBe('0/0');
    });

    it('C2/C3: 200-OK degraded envelope (degraded:true, total:null, missions:[]) is NOT written — last good data retained', () => {
      // Test pin 2 — the bug class the C2 fix closes: a degraded-200
      // response has degraded:true and empty rows + null total.
      // Before C2, the intake would write that envelope verbatim and
      // collapse the badge to "0/0" during an outage. After C2, the
      // canonical helper returns ``null`` and the indicator treats
      // the tick as a no-op against the last good payload.
      const twoPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'leader-a', liveness: 'processing' },
          { mission_id: 'leader-b', liveness: 'paused' },
        ],
        2
      );
      component.applyFetchResult([], [], twoPayload, [], null);
      expect(component.liveMissionCount()).toBe(2);
      expect(component.liveMissionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');

      // Now the BE returns a degraded envelope on the count leg —
      // empty rows, null total, degraded:true. The intake MUST NOT
      // write the count leg's signal. The instances leg is still
      // healthy so the tree payload retains.
      const degradedPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      component.applyFetchResult([], [], degradedPayload, [], null);
      // Last good data retained across the degraded count tick.
      expect(component.liveMissionCount()).toBe(2);
      expect(component.liveMissionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      // The instances payload (empty page) landed — the tree leg is
      // independent of the missions leg's degradation. A subsequent
      // healthy tick re-syncs the count.
      expect(component.lastInstancesPayload().length).toBe(0);

      // The next healthy tick DOES update the count.
      const zeroPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult([], [], zeroPayload, [], null);
      expect(component.liveMissionCount()).toBe(0);
      expect(component.liveMissionsList().length).toBe(0);
      expect(component.displayText()).toBe('0/0');
    });

    it('C3: degraded-200 envelope does NOT zero the count — canonical helper returns null and the signal retains last', () => {
      // Companion to the C2 test: pin the count leg independently.
      // Even if a future bug re-introduced the degraded-200 write,
      // the count helper routes through missionCountFromListResponse
      // and would refuse to zero the signal.
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'leader-a', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], onePayload, onePayload, null);
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
      const twoPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'leader-a', liveness: 'processing' },
          { mission_id: 'leader-b', liveness: 'paused' },
        ],
        2
      );
      component.applyFetchResult([], [], twoPayload, twoPayload, null);
      expect(component.lastIntakeError()).toBeNull();

      // REAL degraded-200 envelope on the count leg: empty rows, null
      // total, degraded:true. The flag is SET on the new leg name.
      const degradedPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      component.applyFetchResult([], [], degradedPayload, [], null);
      expect(component.lastIntakeError()).toBe('liveMissions: degraded envelope');
      // C2/C3 retention stays EXACTLY as-is across the same tick.
      expect(component.liveMissionCount()).toBe(2);
      expect(component.liveMissionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      // The instances leg landed independently (empty page, non-null).
      expect(component.lastInstancesPayload().length).toBe(0);
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
          MockJobQueueIndicatorComponent.buildMissionsPayload(
            [{ mission_id: 'leader-a', liveness: 'processing' }],
            1
          ),
          { defer_blocked: false, pending_count: 0, holders: [] }
        );
        const stampedAt = component.lastFetchAt();
        expect(stampedAt).toBe(1_000_000);

        // All-null legs + a degraded missions count envelope: nothing
        // usable returned — the timestamp must stay byte-identical
        // even as the (controlled) wall clock advances.
        nowSpy.mockReturnValue(2_000_000);
        component.applyFetchResult(
          null,
          null,
          MockJobQueueIndicatorComponent.buildMissionsPayload([], undefined, { degraded: true }),
          null,
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
      const zeroPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult([], [], zeroPayload, zeroPayload, null);
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
      component.applyFetchResult([], [], null, null, {
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
      component.applyFetchResult([], [], null, null, {
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
      component.applyFetchResult([], [], null, null, { defer_blocked: true, pending_count: 1, holders: [pausedHolder] });
      expect(component.deferBlockWarning()).not.toBeNull();

      component.applyFetchResult([], [], null, null, null);
      expect(component.deferBlockWarning()).toBeNull();
    });

    it('no render when pending_count is 0 — the gate lives in the helper', () => {
      component.applyFetchResult([], [], null, null, { defer_blocked: false, pending_count: 0, holders: [] });
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

    it('a pending/queued job in activeJobs reaches the panel’s QUEUED section via buildInstanceTree', () => {
      // End-to-end seam pin: the panel receives activeJobs() and
      // routes unattached non-terminal jobs into ``tree().queued``.
      // A pending/queued job with no mission_id MUST land in
      // ``tree().queued`` — the prior running-only filter hid it.
      component.setActiveJobs([
        createMockJob({ job_id: 'q-orphan', status: 'pending', mission_id: null }),
      ]);
      // Mirror the panel's buildInstanceTree call (instances-primary
      // tree, design V1) so the seam is verifiable without an
      // Angular TestBed harness. An empty instances page ⇒ no node
      // matches ⇒ the job is an orphan ⇒ queued.
      const tree = buildInstanceTree(component.instanceRoots(), component.activeJobs(), []);
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
      let componentTs: string;

      beforeAll(() => {
        // Resolve relative to this spec file.
        const path = require('path');
        const fs = require('fs');
        const specDir = __dirname;
        const htmlPath = path.join(specDir, 'job-queue-indicator.component.html');
        templateHtml = fs.readFileSync(htmlPath, 'utf-8');
        // W4 source-drift pin — the indicator's behavioural
        // assertions run against the mirror above; an F-1-style
        // revert of the REAL component (e.g. removing the
        // ``liveness:'processing,pending,paused'`` + ``limit:1``
        // count leg, or swapping the closeMenu / navigate order in
        // ``onFooterClick``) would pass every mirror test. Pin the
        // real component TS so a revert flips a test.
        const tsPath = path.join(specDir, 'job-queue-indicator.component.ts');
        componentTs = fs.readFileSync(tsPath, 'utf-8');
      });

      it('binds the panel to the FULL non-terminal set: [activeJobs]="activeJobs()"', () => {
        expect(templateHtml).toContain('[activeJobs]="activeJobs()"');
      });

      it('does NOT bind [runningJobs] — the old running-only filter must not return', () => {
        expect(templateHtml).not.toContain('[runningJobs]');
      });

      it('binds (footerClick)="onFooterClick()" so the panel footer activation reaches the indicator', () => {
        // T1 pin — without this binding, the panel's footerClick
        // emit fires into the void and the indicator never navigates
        // to /jobs. Mirror tests prove the component method exists;
        // the source-text pin proves the TEMPLATE actually wires it
        // up.
        expect(templateHtml).toContain('(footerClick)="onFooterClick()"');
      });

      it('binds [instances]="instanceRoots()" so the panel sees the instances-primary tree (design V1)', () => {
        // F-5 structural pin, re-anchored: the panel's [instances]
        // input must come from the indicator's ``instanceRoots()``
        // computed (nested from the instances leg payload). A revert
        // that re-binds to a mission leg (or drops the binding) would
        // slip past every behavioural test but leave the template
        // wiring wrong.
        expect(templateHtml).toContain('[instances]="instanceRoots()"');
        // The dropped LEG B must not resurface as a [missions] input.
        expect(templateHtml).not.toContain('[missions]=');
      });

      it('binds (instanceClick)="onInstanceClick($event)" — ROW CLICK = NAVIGATE on instance nodes', () => {
        // Design V1: instance rows navigate. The panel emits
        // ``instanceClick``; the indicator closes the menu and routes
        // to /projects/<key>/instances/<id>.
        expect(templateHtml).toContain('(instanceClick)="onInstanceClick($event)"');
      });

      it('anchors the job-queue menu TOP-RIGHT: xPosition="before" on #jobQueueMenu', () => {
        // W-top-right anchoring pin (fix 2026-09-08). The button sits
        // near the right viewport edge; Material's default
        // ``xPosition='after'`` connects the overlay's LEFT (start)
        // edge to the trigger, so the 560px panel extended rightward
        // off-screen (reproduced: menu rect.right 1304 but panel
        // rect.right 1584 at 1440vw). ``before`` → originX/overlayX
        // 'end' in menu.mjs _setPosition → the panel's RIGHT edge
        // aligns with the button's right edge and opens leftward+down
        // (yPosition stays the default 'below'). Assert against the
        // #jobQueueMenu tag specifically so the defer-holder menu
        // (which legitimately keeps the default) can't satisfy this.
        const menuTag = templateHtml.match(/<mat-menu[^>]*#jobQueueMenu[^>]*>/)?.[0] ?? '';
        expect(menuTag).toContain('#jobQueueMenu');
        expect(menuTag).toContain('xPosition="before"');
        expect(menuTag).not.toContain('xPosition="after"');
      });
    });

    // W-top-right + W-zero-h-scroll — the dropdown SHELL contract in
    // the GLOBAL stylesheet. Angular Material 21 ships
    // `.mat-mdc-menu-panel { max-width: 280px; overflow: auto }` as a
    // global rule (ViewEncapsulation.None), and MatMenu copies the
    // host `class="job-queue-dropdown"` ONTO the .mat-mdc-menu-panel
    // element (host class → _classList → [class] binding). The CDK
    // overlay mounts at <body>, so component ::ng-deep can never
    // reach it — the widening rule lives in src/styles.scss. This pin
    // reads the REAL stylesheet: dropping or renaming the rule
    // re-creates the user-reported horizontal scroll (shell 280px vs
    // 560px panel) with every behavioural test still green.
    describe('job-queue dropdown shell sizing (styles.scss global pin)', () => {
      let stylesScss: string;

      beforeAll(() => {
        const path = require('path');
        const fs = require('fs');
        const stylesPath = path.join(__dirname, '..', '..', '..', 'styles.scss');
        stylesScss = fs.readFileSync(stylesPath, 'utf-8');
      });

      it('widens the mat-menu SHELL for .job-queue-dropdown (Material default cap is max-width: 280px)', () => {
        const rule = stylesScss.match(/\.mat-mdc-menu-panel\.job-queue-dropdown\s*\{[^}]*\}/)?.[0] ?? '';
        expect(rule).toContain('max-width: calc(100vw - 16px)');
        expect(rule).toContain('overflow-x: hidden');
      });
    });

    // W4 — source-drift pins on the REAL component TS. Mirror tests
    // prove behaviour against the same logic; these pins prove the
    // REAL component still has the wiring the badge depends on.
    // An F-1-style revert (e.g. dropping LEG A's filter, swapping
    // closeMenu/navigate order, or reverting LEG A back to limit:1)
    // would slip past mirror tests but flip at least one of these
    // assertions.
    describe('component TS source-drift pins', () => {
      let componentTs: string;

      beforeAll(() => {
        const path = require('path');
        const fs = require('fs');
        const specDir = __dirname;
        const tsPath = path.join(specDir, 'job-queue-indicator.component.ts');
        componentTs = fs.readFileSync(tsPath, 'utf-8');
      });

      it('LEG A (live) still uses liveness: \'processing,pending,paused\' (live-only filter)', () => {
        // LEG A is the single source of truth for the badge count,
        // panel LIVE MISSIONS rows, AND the tooltip's per-liveness
        // breakdown. Removing the liveness filter would re-introduce
        // the F-5 bug class where LEG B's terminal rows silently
        // inflate the live count + breakdown.
        expect(componentTs).toContain("liveness: 'processing,pending,paused'");
      });

      it('LEG A still uses limit: 20 (was 1; F-5 unification needs the full live page)', () => {
        // F-5 fix: LEG A is no longer a cheap probe. It now pulls the
        // FULL live page (``limit: 20``) so the panel's LIVE MISSIONS
        // rows and the tooltip's per-liveness breakdown both have
        // rows to work with — a ``limit: 1`` probe only returned one
        // row, which couldn't drive both consumers from a single
        // source. The count comes from ``total ?? missions.length`` so
        // the badge still survives the >20 ceiling.
        expect(componentTs).toContain('limit: 20');
      });

      it('LEG A does NOT use limit: 1 (the old probe would re-introduce the F-5 contradiction)', () => {
        // Structural anti-pin: a revert of LEG A's limit back to 1
        // would pass the ``limit: 20`` positive pin above (since both
        // would co-exist) but would silently re-introduce the F-5
        // bug: a 1-row live page cannot be the source for both the
        // count AND the panel's full live rows. Pin the ABSENCE of
        // ``limit: 1`` on LEG A's wiring.
        // Match the exact snippet from the live leg.
        expect(componentTs).not.toMatch(/liveness:\s*'processing,pending,paused',\s*\n\s*limit:\s*1/);
      });

      it('LEG B (recent content leg) is GONE — the unfiltered listMissions probe must NOT return', () => {
        // Instances-primary tree (design V1): the unfiltered LEG B
        // content page was DROPPED — recent terminal mission nodes
        // are replaced by terminal roots from the instances page. A
        // revert that re-adds the probe would re-introduce the
        // dropped machinery (and the F-5 contradiction surface).
        // Code-shaped needles (``this.jobService.`` receiver +
        // ``recentMissionsPayload`` signal) so doc-comment mentions
        // of the retired leg don't false-positive.
        expect(componentTs).not.toContain('this.jobService.listMissions({ limit: 20 })');
        expect(componentTs).not.toContain('recentMissionsPayload');
      });

      it('instances leg uses InstanceService.listInstanceTree(10) (root-paginated page)', () => {
        // Design V1 leg: GET /api/instances?limit=10 via the
        // instances service. Dropping the leg (or shrinking it to a
        // page-less probe) would starve the panel's tree.
        expect(componentTs).toContain('this.instanceService.listInstanceTree(10)');
      });

      it('instances leg opts OUT of descendant loading (POLL-SPAM FIX)', () => {
        // The badge polls every 8s; with include_descendants=true (the
        // historical default), the BE BFS-loads the full subtree of every
        // root in the page — prod (~6,324 instances) blows past
        // MAX_DESCENDANTS_PER_PAGE=1000 on every tick (~510 WARN/hr).
        //
        // The fix has TWO halves:
        //   (1) The component calls ``InstanceService.listInstanceTree(10)``
        //       (already pinned above) — the SERVICE owns the wire contract.
        //   (2) The service must pass ``include_descendants=false`` to the
        //       API call (pinned in ``instance.service.spec.ts``).
        //
        // This test pins the COMPONENT half: the badge MUST use the
        // listInstanceTree path (which carries the include_descendants
        // contract). If a future refactor routes the badge through a
        // different API call (e.g. a dedicated tree endpoint), the service
        // contract disappears and the badge must be re-pinned to whatever
        // the new path is.
        //
        // Specifically: the badge's instances leg calls
        // ``this.instanceService.listInstanceTree(10)`` (the SERVICE path
        // that owns include_descendants). It MUST NOT call
        // ``api.listInstances(...)`` directly — that's a tree-builder
        // concern, not a badge concern.
        expect(componentTs).toContain('this.instanceService.listInstanceTree(10)');
        // Anti-pin: the component must NOT bypass the service and call
        // ``api.listInstances(...)`` directly. The whole point of the
        // service wrapper is to centralize the include_descendants
        // contract.
        expect(componentTs).not.toMatch(/this\.api\.listInstances\(/);
      });

      it('onInstanceClick closes the menu BEFORE mutating tab state and navigates to the instance', () => {
        // Same order lock as onJobClick/onFooterClick: the surface
        // drops first, then route state mutates.
        const closeIdx = componentTs.indexOf('onInstanceClick(node: InstanceNode): void');
        const navigateIdx = componentTs.indexOf("'/projects',", closeIdx);
        expect(closeIdx).toBeGreaterThan(-1);
        expect(navigateIdx).toBeGreaterThan(closeIdx);
        expect(componentTs).toContain("this.router.navigate([\n      '/projects',");
      });

      it('onFooterClick closes the menu BEFORE navigating to /jobs (order-locked)', () => {
        // The same flow as ``onJobClick``: drop the surface first,
        // then mutate route state so the user sees the menu
        // disappear before the page transition. Swapping the order
        // would race the menu close with the route change and leave
        // a flash of the menu over the new page.
        const closeIdx = componentTs.indexOf('this.menuTrigger?.closeMenu()');
        const navigateIdx = componentTs.indexOf("this.router.navigate(['/jobs'])");
        expect(closeIdx).toBeGreaterThan(-1);
        expect(navigateIdx).toBeGreaterThan(closeIdx);
      });

      it('applyFetchResults actually writes the LEG A payload (F-5 production write-site)', () => {
        // F-5 closure (2026-09-08) — pin the REAL production TS for
        // the LEG A payload write. The mirror above performs this
        // write too (proves the wiring is correct), but if production
        // ever drifts back to a count-only write both ``liveMissionsList``
        // (panel LIVE MISSIONS rows) and ``liveMissionBreakdown``
        // (tooltip per-liveness split) read an always-empty
        // ``liveMissionsPayload`` → the panel's LIVE section stays
        // empty while the badge reports live missions. The bug class
        // has now slipped past review twice; this pin flips a test on
        // any future mirror-only fix.
        expect(componentTs).toContain('liveMissionsPayload.set(');
      });

      it('applyFetchResults actually writes the instances payload (F-5 production set-site, design V1)', () => {
        // THE F-5 LESSON PIN (this class escaped twice before) — pin
        // the REAL production TS for the instances tree payload
        // write. The mirror performs the equivalent write (proves
        // the wiring), but if production drifts back to a
        // never-written signal, ``instanceRoots()`` derives from an
        // always-empty array → the panel's LIVE CONVERSATIONS +
        // RECENT sections stay empty forever while the badge and
        // every mirror test stay green. The pin names the EXACT
        // production write text inside ``applyFetchResults`` — any
        // rename/move/revert flips this test.
        expect(componentTs).toContain('this.instancesPayload.set(instances);');
        // And the write must feed the derived tree input the
        // template binds: instancesPayload → instanceRoots.
        expect(componentTs).toContain('buildInstanceNodes(this.instancesPayload())');
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

  describe('onInstanceClick (instances-primary tree — ROW CLICK = NAVIGATE)', () => {
    const mirror = () => MockJobQueueIndicatorComponent;

    function mkNode(over: Partial<InstanceRow>): InstanceNode {
      const row: InstanceRow = {
        instance_id: 'i-1',
        agent_id: 'leader',
        agent_tag: null,
        status: 'running',
        parent_id: null,
        title: null,
        initiative_message: null,
        children: [],
        created_at: '2026-09-08T09:00:00Z',
        updated_at: '2026-09-08T10:00:00Z',
        project_id: 'p-1',
        pinned: false,
        color_tag: null,
        icon_tag: null,
        pinned_at: null,
        ...over,
      };
      return { instance: row, children: [], attachedJobs: [] };
    }

    it('should close the menu before deciding tab/navigation', () => {
      component.menuClosedAfterClick = false;
      component.onInstanceClick(mkNode({}));
      expect(component.menuClosedAfterClick).toBe(true);
    });

    it('should open the project tab (resolved from projectNameMap) and navigate to the instance', () => {
      component.setProjectNameMap(new Map([['p-1', 'My Project']]));
      component.onInstanceClick(mkNode({ instance_id: 'i-abc', project_id: 'p-1' }));
      expect(component.lastTabAction).toEqual({ kind: 'add', project_id: 'p-1', name: 'My Project' });
      expect(component.lastInstanceNavigated).toEqual(['/projects', 'p-1', 'instances', 'i-abc']);
    });

    it('should fall back to first-8-chars of project_id when the name is missing from projectNameMap', () => {
      component.onInstanceClick(mkNode({ instance_id: 'i-abc', project_id: 'p123456789' }));
      expect(component.lastTabAction).toEqual({ kind: 'add', project_id: 'p123456789', name: 'p1234567' });
      expect(component.lastInstanceNavigated).toEqual(['/projects', 'p123456789', 'instances', 'i-abc']);
    });

    it('should setActiveTab("all") when project_id is null — the same null-project fallback as onJobClick', () => {
      component.onInstanceClick(mkNode({ instance_id: 'i-abc', project_id: null }));
      expect(component.lastTabAction).toEqual({ kind: 'setActive', tabId: 'all' });
      expect(component.lastInstanceNavigated).toEqual(['/projects', 'all', 'instances', 'i-abc']);
    });

    it('works identically for a CHILD instance node (nav target is the child id)', () => {
      const child = mkNode({ instance_id: 'i-kid', parent_id: 'i-root' });
      component.onInstanceClick(child);
      expect(component.lastInstanceNavigated).toEqual(['/projects', 'p-1', 'instances', 'i-kid']);
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

      const zeroPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult([], [], zeroPayload, zeroPayload, {
        defer_blocked: false,
        pending_count: 0,
        holders: [],
      });
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

      // Degraded-200 missions count tick — every leg non-null, but
      // the count envelope is degraded. The flag must NOT be
      // cleared; the new leg name surfaces the degradation.
      const degradedPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      component.applyFetchResult([], [], degradedPayload, null, {
        defer_blocked: false,
        pending_count: 0,
        holders: [],
      });
      expect(component.lastIntakeError()).toBe('liveMissions: degraded envelope');

      // Recovery contract unchanged: the next FULLY clean tick
      // (non-null + non-degraded on EVERY leg including both missions
      // legs) clears the flag.
      const zeroPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult([], [], zeroPayload, zeroPayload, {
        defer_blocked: false,
        pending_count: 0,
        holders: [],
      });
      expect(component.lastIntakeError()).toBeNull();
    });

    it('passes a null leg through applyFetchResult without resetting (W-jobs-intake honesty)', () => {
      // The forkJoin per-leg catchError turns each failure into
      // ``null``. The applyFetchResult handler MUST NOT reset the
      // job list to ``[]`` on null — that's the impersonation gap.
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);

      component.applyFetchResult(null, null, null, null, null);

      expect(component.displayText()).toBe('1/1');
      expect(component.runningJobs().length).toBe(1);
      expect(component.recentJobs().length).toBe(1);
    });

    it('RETAINS the missions count across a jobs-fetch error (no false bare 0/0)', () => {
      // A live leader mission is proven by the missions projection;
      // the jobs intake then errors. The badge must show the last
      // good "missions: N" — NEVER a false bare 0/0 idle.
      const twoPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'leader-a', liveness: 'processing' }],
        2
      );
      component.applyFetchResult([], [], twoPayload, twoPayload, null);
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
      // jobs intake still emits. F-5 fix: both missions legs (LEG A live +
      // LEG B recent) isolate independently — a LEG A 500 must not kill
      // LEG B or the jobs intake.
      const result = await firstValueFrom(forkJoin({
        active: of([createMockJob({ status: 'processing' })]),
        liveMissions: throwError(() => new Error('liveMissions 500')).pipe(catchError(() => of(null))),
        recentMissions: throwError(() => new Error('recentMissions 500')).pipe(catchError(() => of(null))),
        deferBlocked: throwError(() => new Error('defer-blocked 404')).pipe(catchError(() => of(null))),
      }));
      expect(result.active.length).toBe(1); // jobs intake survived all failures
      expect(result.liveMissions).toBeNull();
      expect(result.recentMissions).toBeNull();
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
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'm-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], onePayload, onePayload, null);
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
      const twoPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'm-1', liveness: 'processing' },
          { mission_id: 'm-2', liveness: 'paused' },
        ],
        2
      );
      component.applyFetchResult([], [], twoPayload, twoPayload, null);
      expect(component.missionsSegmentText()).toBe('2');
    });
  });

  describe('segmented pill — liveMissionBreakdown (per-liveness counts)', () => {
    it('tallies processing / pending / paused from the missions list', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
        { mission_id: 'm-2', liveness: 'processing' },
        { mission_id: 'm-3', liveness: 'paused' },
        { mission_id: 'm-4', liveness: 'pending' },
      ]);
      component.applyFetchResult([], [], payload, payload, null);
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

  describe('liveMissionsList + instancesPayload — feed the tooltip and the panel tree', () => {
    it('liveMissionsList exposes the latest successful LEG A rows (tooltip breakdown source)', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
        { mission_id: 'm-2', liveness: 'paused' },
      ]);
      component.applyFetchResult([], [], payload, [], null);
      expect(component.liveMissionsList().length).toBe(2);
      expect(component.liveMissionsList().map((m) => m.mission_id).sort()).toEqual(['m-1', 'm-2']);
    });

    it('liveMissionsList retains the last known rows across a degraded poll', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
      ]);
      component.applyFetchResult([], [], payload, [], null);
      component.applyFetchResult([], [], null, null, null);
      expect(component.liveMissionsList().length).toBe(1);
      expect(component.liveMissionsList()[0].mission_id).toBe('m-1');
    });

    it('instancesPayload exposes the latest successful instances page (panel tree source)', () => {
      const rows = MockJobQueueIndicatorComponent.buildInstanceRows([
        { instance_id: 'i-1' },
        { instance_id: 'i-2', status: 'completed' as const },
      ]);
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
      ]);
      component.applyFetchResult([], [], payload, rows, null);
      expect(component.lastInstancesPayload().length).toBe(2);
      expect(component.instanceRoots().length).toBe(2);
    });
  });

  // ── F-5 mission-tree single-source pin — historical (2026-09-08) ──────────
  //
  // F-5 (closed): the badge's live-mission count came from the
  // filter-aware LEG A (``limit: 20``, total = N live), but the
  // tooltip's per-liveness breakdown AND the panel's LIVE MISSIONS
  // rows derived from the unfiltered LEG B (``limit: 20``, top-20 by
  // ``last_activity``). When the unfiltered page's top-20 happened to
  // be all-terminal, the badge read "7" while the live section was
  // empty AND the breakdown said "(processing 0, pending 0, paused 0)"
  // — a structural contradiction (tester-dataset repro).
  //
  // The fix unified the live consumers on ONE source (LEG A) so every
  // live row the badge counts MUST appear in the live section AND
  // tally into the breakdown.
  //
  // Historical note: the LEG B content page was DROPPED entirely in
  // commit a895cac5 when the panel migrated to the instances-primary
  // tree (``feature/job-queue-instance-tree``, design V1). Recent
  // terminal mission nodes were replaced by terminal roots of the
  // instances page; the unfiltered LEG B poll is no longer wired.
  // LEG A is the sole mission feed that remains (badge + tooltip).
  // The instances leg (``/api/instances?limit=10``) now feeds the
  // panel tree.

  describe('F-5 legacy: badge + tooltip still read from LEG A; the panel tree reads the instances leg', () => {
    it('badge shows the LEG A total (live=7) even when the instances page carries many terminal roots', () => {
      // Former F-1/F-5 carryover, re-anchored on the new leg pair:
      // live count = 7 (filter-aware total from LEG A); the
      // instances leg returns a page of 82 rows (mostly terminal
      // conversations). The badge MUST use LEG A — never the
      // instances page — so the visible number stays honest.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'live-1', liveness: 'processing' },
          { mission_id: 'live-2', liveness: 'processing' },
          { mission_id: 'live-3', liveness: 'pending' },
          { mission_id: 'live-4', liveness: 'paused' },
          { mission_id: 'live-5', liveness: 'paused' },
          { mission_id: 'live-6', liveness: 'processing' },
          { mission_id: 'live-7', liveness: 'processing' },
        ],
        7
      );
      const instanceRows = MockJobQueueIndicatorComponent.buildInstanceRows(
        Array.from({ length: 82 }, (_, i) => ({
          instance_id: `i-${i}`,
          status: (i < 7 ? 'running' : 'completed') as InstanceRow['status'],
        }))
      );
      component.applyFetchResult([], [], livePayload, instanceRows, null);
      // Badge shows 7 (LEG A), NOT 82 (instances page size).
      expect(component.liveMissionCount()).toBe(7);
      expect(component.missionsSegmentText()).toBe('7');
      // Breakdown reads from LEG A only — 4 processing, 2 paused, 1
      // pending. The instances page never contributes to the
      // tooltip's per-liveness split.
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(4);
      expect(bd['paused']).toBe(2);
      expect(bd['pending']).toBe(1);
      // Panel tree input = the nested instance roots (82 flat rows →
      // 82 roots — none nested in this fixture).
      expect(component.instanceRoots().length).toBe(82);
      // Sanity pin: LEG A's payload is preserved as-is.
      expect(component.lastLiveMissionsPayload()!.total).toBe(7);
      expect(component.lastInstancesPayload().length).toBe(82);
    });

    it('live count is correct when live missions > 20 (count comes from filtered total, NOT the 20-item page)', () => {
      // 25 live missions. The backend clamps to ``limit:20`` so LEG A
      // returns its top 20 rows (with ``total:25``); the badge's
      // count reads ``total`` so it shows 25, NOT 20 (the page size).
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 20 }, (_, i) => ({
          mission_id: `live-${i}`,
          liveness: 'processing' as const,
        })),
        25
      );
      component.applyFetchResult([], [], livePayload, [], null);
      // Badge shows 25 (LEG A's ``total``), NOT 20 (LEG A's page size).
      expect(component.liveMissionCount()).toBe(25);
      expect(component.missionsSegmentText()).toBe('25');
      // Breakdown covers the fetched 20 of LEG A — the cosmetic S4
      // ceiling (live > 20): count uses LEG A's ``total`` (=25) so
      // the badge stays honest; breakdown covers LEG A's fetched 20
      // rows (the page ceiling).
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(20);
    });

    it('LEG A degraded envelope retains the LAST good live count (no false bare 0/0)', () => {
      // Seed a healthy live payload.
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], onePayload, [], null);
      expect(component.liveMissionCount()).toBe(1);
      // Now LEG A degrades (the instances leg is still healthy so
      // the panel keeps its tree).
      const degradedLive = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      component.applyFetchResult([], [], degradedLive, [], null);
      // The flag is set (LEG A degraded) AND the count signal
      // retains the last good value.
      expect(component.lastIntakeError()).toBe('liveMissions: degraded envelope');
      expect(component.liveMissionCount()).toBe(1);
    });

    it('LEG A catchError failure does NOT kill the instances leg (leg independence)', () => {
      // Seed a healthy state (1 live mission + 1 root).
      const healthy = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], healthy, MockJobQueueIndicatorComponent.buildInstanceRows([{ instance_id: 'i-a' }]), null);
      expect(component.instanceRoots().length).toBe(1);
      // Now LEG A fails (per-leg catchError fires FIRST — sets
      // ``lastIntakeError`` — and the forkJoin next-handler receives
      // ``liveMissions: null``). The instances leg is still healthy
      // so it UPDATES the tree payload independently.
      component.onLegError('liveMissions', new Error('count 500'));
      const updatedRows = MockJobQueueIndicatorComponent.buildInstanceRows([
        { instance_id: 'i-a' },
        { instance_id: 'i-b', status: 'completed' as const },
      ]);
      component.applyFetchResult([], [], null, updatedRows, null);
      // The flag remains set from the catchError call — LEG A's
      // error is recorded.
      expect(component.lastIntakeError()).toBe('liveMissions: count 500');
      // The instances leg updated — the tree reflects the new page
      // (2 roots) while the badge retains its last good count (1).
      expect(component.instanceRoots().length).toBe(2);
      expect(component.liveMissionCount()).toBe(1);
    });

    it('instances leg failure retains the LAST good tree (panel never flashes empty)', () => {
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      const seeded = MockJobQueueIndicatorComponent.buildInstanceRows([{ instance_id: 'i-a' }]);
      component.applyFetchResult([], [], onePayload, seeded, null);
      expect(component.instanceRoots().length).toBe(1);
      // Now the instances leg fails — the per-leg catchError fires
      // first (recorded), then the next-handler receives ``null`` —
      // while LEG A is still healthy.
      component.onLegError('instances', new Error('tree 500'));
      component.applyFetchResult([], [], onePayload, null, null);
      expect(component.lastIntakeError()).toBe('instances: tree 500');
      // The tree payload retains the last good value — the panel
      // never flashes empty.
      expect(component.instanceRoots().length).toBe(1);
    });

    it('instances catchError failure does NOT kill LEG A (leg independence)', () => {
      // Seed a healthy live payload.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], livePayload, [], null);
      expect(component.liveMissionCount()).toBe(1);
      // Now the instances leg fails; LEG A is still healthy so it
      // UPDATES the badge's N.
      component.onLegError('instances', new Error('list 500'));
      const updatedLive = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        3
      );
      component.applyFetchResult([], [], updatedLive, null, null);
      // The flag remains set from the catchError call — the
      // instances leg's error is recorded.
      expect(component.lastIntakeError()).toBe('instances: list 500');
      // LEG A updated — the badge's N reflects the new count.
      expect(component.liveMissionCount()).toBe(3);
    });
  });

  // ── F-5 legacy pins re-anchored on the instances-primary tree ────────
  //
  // The original three pins proved the LEG A/LEG B split was
  // structurally sound. LEG B is GONE (replaced by the instances
  // page); the pins survive re-anchored on the new invariant set:
  //
  //   * single-source pin — badge count + tooltip breakdown remain
  //     LEG-A-only; the instances page feeds ONLY the tree.
  //   * tester-dataset pin — the legacy tester dataset (7 live
  //     missions, all-terminal unfiltered page) still produces a
  //     consistent badge + tooltip, and the tree payload is set.
  //   * retention pin — an instances-leg failure retains the last
  //     good tree and never fabricates an empty one (the F-5
  //     "false idle" class, tree edition).

  describe('F-5 single-source pin: badge + breakdown read LEG A; the tree reads the instances leg', () => {
    it('badge count reads from LEG A — header N is total ?? missions.length of the live page', () => {
      // LEG A = 7 live (total=7); the instances page = 82 roots.
      // The badge MUST read 7 from LEG A's total — never from the
      // instances page's size.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 7 }, (_, i) => ({
          mission_id: `live-${i}`,
          liveness: 'processing' as MissionLiveness,
        })),
        7
      );
      const instanceRows = MockJobQueueIndicatorComponent.buildInstanceRows(
        Array.from({ length: 82 }, (_, i) => ({ instance_id: `i-${i}` }))
      );
      component.applyFetchResult([], [], livePayload, instanceRows, null);
      // Single-source assertion #1: badge N = LEG A's total (7), NOT
      // the instances page size (82).
      expect(component.liveMissionCount()).toBe(7);
      expect(component.missionsSegmentText()).toBe('7');
      // Sanity: LEG A's payload is the one being read for the count.
      expect(component.lastLiveMissionsPayload()!.total).toBe(7);
    });

    it('liveMissionBreakdown reads from LEG A — the instances page never contributes', () => {
      // LEG A = 3 processing + 2 paused (5 total live). The
      // instances page carries 20 terminal roots that would add
      // nothing to the breakdown anyway — the point is the
      // breakdown counts LEG A and nothing else.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'live-1', liveness: 'processing' },
          { mission_id: 'live-2', liveness: 'processing' },
          { mission_id: 'live-3', liveness: 'processing' },
          { mission_id: 'live-4', liveness: 'paused' },
          { mission_id: 'live-5', liveness: 'paused' },
        ],
        5
      );
      const instanceRows = MockJobQueueIndicatorComponent.buildInstanceRows(
        Array.from({ length: 20 }, (_, i) => ({
          instance_id: `done-${i}`,
          status: 'completed' as const,
        }))
      );
      component.applyFetchResult([], [], livePayload, instanceRows, null);
      // Single-source assertion #2: breakdown reflects LEG A only
      // (3 processing + 2 paused = 5).
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(3);
      expect(bd['paused']).toBe(2);
      expect(bd['pending']).toBe(0);
      expect(bd['processing'] + bd['paused'] + bd['pending']).toBe(5);
      // Sanity: the LEG A payload is what's being read.
      expect(component.liveMissionsList().length).toBe(5);
    });

    it('the panel tree input comes from the instances leg — end-to-end payload→roots write', () => {
      // F-5 lesson class: the payload signal must be WRITTEN by the
      // intake so the derived roots actually change. A flat page
      // with a parent and its child nests into ONE root with one
      // child.
      const instanceRows = MockJobQueueIndicatorComponent.buildInstanceRows([
        { instance_id: 'root-1', children: ['kid-1'] },
        { instance_id: 'kid-1', parent_id: 'root-1' },
        { instance_id: 'solo' },
      ]);
      component.applyFetchResult([], [], null, instanceRows, null);
      expect(component.lastInstancesPayload().length).toBe(3);
      expect(component.instanceRoots().length).toBe(2); // root-1 (nested kid-1) + solo
      expect(component.instanceRoots()[0].children[0].instance.instance_id).toBe('kid-1');
    });
  });

  describe('F-5 tester-dataset pin: 7 live + all-terminal instances page → consistent badge, tooltip, tree', () => {
    it('badge + breakdown + tooltip stay consistent and the instances payload lands', () => {
      // Tester-dataset repro, re-anchored: LEG A has 7 live with a
      // real mix (processing 3, pending 2, paused 2); the instances
      // page is entirely terminal conversations (the shape that
      // used to trigger the F-5 self-contradiction through LEG B).
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'live-1', liveness: 'processing' },
          { mission_id: 'live-2', liveness: 'processing' },
          { mission_id: 'live-3', liveness: 'processing' },
          { mission_id: 'live-4', liveness: 'pending' },
          { mission_id: 'live-5', liveness: 'pending' },
          { mission_id: 'live-6', liveness: 'paused' },
          { mission_id: 'live-7', liveness: 'paused' },
        ],
        7
      );
      const instanceRows = MockJobQueueIndicatorComponent.buildInstanceRows(
        Array.from({ length: 20 }, (_, i) => ({
          instance_id: `done-${i}`,
          status: 'completed' as const,
        }))
      );
      component.applyFetchResult([], [], livePayload, instanceRows, null);
      // Header: badge shows 7 (LEG A total), NOT 20 (page size).
      expect(component.liveMissionCount()).toBe(7);
      expect(component.missionsSegmentText()).toBe('7');
      // Tooltip: real split from LEG A (3 + 2 + 2).
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(3);
      expect(bd['pending']).toBe(2);
      expect(bd['paused']).toBe(2);
      const tt = component.tooltipText();
      expect(tt).toContain('Live missions: 7 (processing 3, pending 2, paused 2)');
      // Tree: the instances payload landed (20 terminal roots — the
      // RECENT section's source).
      expect(component.lastInstancesPayload().length).toBe(20);
      expect(component.instanceRoots().length).toBe(20);
    });
  });

  describe('instances-leg retention pin: a failed leg never fabricates an empty tree', () => {
    it('a null instances leg leaves the previous payload untouched while other legs keep flowing', () => {
      // Seed a good tree (2 roots, one nested).
      const seeded = MockJobQueueIndicatorComponent.buildInstanceRows([
        { instance_id: 'root', children: ['kid'] },
        { instance_id: 'kid', parent_id: 'root' },
      ]);
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], onePayload, seeded, null);
      expect(component.instanceRoots().length).toBe(1);
      expect(component.instanceRoots()[0].children.length).toBe(1);
      // Next tick: the instances leg FAILS — the per-leg catchError
      // fires first (recorded like the real component's
      // ``recordLegError``) — and the forkJoin next-handler receives
      // ``instances: null``. The tree payload MUST retain the last
      // good value.
      component.onLegError('instances', new Error('instances 500'));
      component.applyFetchResult(
        [createMockJob({ status: 'processing' })],
        [createMockJob({ status: 'completed' })],
        onePayload,
        null,
        null
      );
      expect(component.lastIntakeError()).toBe('instances: instances 500');
      expect(component.instanceRoots().length).toBe(1);
      expect(component.instanceRoots()[0].children[0].instance.instance_id).toBe('kid');
      // The healthy jobs legs still updated.
      expect(component.activeJobs().length).toBe(1);
      expect(component.recentJobs().length).toBe(1);
    });
  });

  // ── T1 panel footer — "Open full queue →" navigation ────────────────

  describe('T1: onFooterClick closes the menu and routes to /jobs', () => {
    it('closes the menu and routes to /jobs (the dedicated Jobs page)', () => {
      component.onFooterClick();
      expect(component.menuClosedAfterClick).toBe(true);
      expect(component.lastFooterNavigated).toEqual(['/jobs']);
    });

    it('does NOT mutate lastTabAction (footer navigation has no project context)', () => {
      // onJobClick's tab-decision logic should NOT fire from the
      // footer activation — the footer is a navigation-only action
      // with no project context. ``lastTabAction`` stays null.
      component.lastTabAction = null;
      component.onFooterClick();
      expect(component.lastTabAction).toBeNull();
    });

    it('does NOT mutate lastNavigated (the indicator route is the source of truth for footer navigation)', () => {
      // The footer navigation lands on ``lastFooterNavigated`` (its
      // own captured side-effect), NOT on ``lastNavigated`` (which
      // is onJobClick's surface). This keeps the two navigation
      // paths cleanly separable for the spec.
      component.lastNavigated = null;
      component.onFooterClick();
      expect(component.lastNavigated).toBeNull();
      expect(component.lastFooterNavigated).toEqual(['/jobs']);
    });
  });
});
