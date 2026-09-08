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
   * F-5 mirror (2026-09-08) — TWO raw missions-payload signals.
   *
   * ``_liveMissionsPayload`` mirrors the LEG A
   * (``listMissions({ liveness: 'processing,pending,paused',
   * limit: 20 })``) response. The single source of truth for the
   * badge count + panel LIVE MISSIONS rows + tooltip per-liveness
   * breakdown. Mirrors the real component's ``liveMissionsPayload``.
   *
   * ``_recentMissionsPayload`` mirrors the LEG B
   * (``listMissions({ limit: 20 })``) response — the unfiltered
   * page that feeds the panel's terminal mission nodes + recentFlat
   * ONLY. Mirrors the real component's ``recentMissionsPayload``.
   *
   * The mirror exposes LEG A as ``lastLiveMissionsPayload`` and LEG
   * B as ``lastRecentMissionsPayload`` (both read-only) so the F-5
   * pins can assert the per-leg retention semantics independently.
   */
  private readonly _liveMissionsPayload = signal<MissionListResponse | null>(null);
  readonly lastLiveMissionsPayload = this._liveMissionsPayload.asReadonly();

  private readonly _recentMissionsPayload = signal<MissionListResponse | null>(null);
  readonly lastRecentMissionsPayload = this._recentMissionsPayload.asReadonly();

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
   * F-5 mirror — the panel's ``[missions]`` input. Composed of LEG A
   * rows + LEG B rows filtered to terminal liveness so the two sets
   * are disjoint by construction. Mirrors the real component's
   * ``missionsList``.
   */
  missionsList = computed<MissionSummary[]>(() => {
    const live = this.liveMissionsList();
    const recentPayload = this.lastRecentMissionsPayload();
    const recentOnly = (recentPayload?.missions ?? []).filter(
      (m) => m.liveness !== null && m.liveness !== 'processing' && m.liveness !== 'pending' && m.liveness !== 'paused'
    );
    return [...live, ...recentOnly];
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
   * honesty + F-5 mission-leg single-source pin):
   * - ``active`` / ``recent`` may be ``null`` (per-leg catchError
   *   swallowed a failure); on ``null`` we RETAIN the previous list
   *   rather than resetting to ``[]``;
   * - ``liveMissions === null`` (degraded live leg / 404-skew
   *   failure) RETAINS the previous live count + payload — never
   *   falsely idle; the panel's LIVE MISSIONS rows + tooltip
   *   breakdown stay sourced from LEG A so the F-5 bug class
   *   stays closed;
   * - ``recentMissions === null`` (degraded recent leg / 404-skew
   *   failure) RETAINS the previous payload — the panel never
   *   flashes empty;
   * - F-5 fix: ``liveMissions`` and ``recentMissions`` are
   *   independent. A degraded envelope on ONE does not touch the
   *   OTHER's last good payload. Each is reported via ``onLegError``
   *   with its own leg name so the UI can flag which projection
   *   degraded.
   * - C2 fix: a 200-OK ``degraded:true`` envelope on EITHER missions
   *   leg ALSO retains the previous payload and DOES NOT touch the
   *   corresponding signal;
   * - C3 fix: ``liveMissionCountRaw`` is updated only via the
   *   canonical ``missionCountFromListResponse`` helper, on a
   *   non-degraded LEG A tick;
   * - degraded-200 flag parity: a non-null ``degraded:true`` envelope
   *   on EITHER missions leg ALSO raises ``lastIntakeError`` via
   *   ``onLegError`` (mirroring the component's ``recordLegError``)
   *   and counts as a FAILED leg for BOTH the clear gate and the
   *   ``lastFetchAt`` freeze gate;
   * - ``deferBlocked === null`` hides the warning; a payload is run
   *   through the canonical ``deferBlockIndicator`` helper;
   * - ``lastFetchAt`` advances only when at least one leg succeeded
   *   (either missions leg: non-degraded).
   *
   * F-5 (2026-09-08, mission-tree single-source pin) — LEG A is
   * ``liveMissions`` (filter-aware, liveness=processing,pending,paused,
   * limit=20) and is the SINGLE source of truth for the badge count +
   * panel LIVE MISSIONS rows + tooltip per-liveness breakdown. LEG B
   * is ``recentMissions`` (unfiltered, limit=20) and feeds the
   * panel's RECENT terminal mission nodes + recentFlat ONLY. Closes
   * the "header ● 7, live section empty" self-contradiction where
   * the unfiltered page's top-20 happened to be all-terminal.
   */
  applyFetchResult(
    active: Job[] | null,
    recent: Job[] | null,
    liveMissions: MissionListResponse | null,
    recentMissions: MissionListResponse | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    if (active !== null) this._activeJobs.set(active);
    if (recent !== null) this.allRecentJobs.set(recent);
    if (liveMissions !== null && !liveMissions.degraded) {
      const count = missionCountFromListResponse(liveMissions);
      this.liveMissionCountRaw.set(count);
      // F-5 closure (2026-09-08) — also store LEG A's payload so
      // ``liveMissionsList`` (panel LIVE MISSIONS rows) and
      // ``liveMissionBreakdown`` (tooltip per-liveness split) can
      // read its ``missions`` rows. Mirrors the production write at
      // job-queue-indicator.component.ts inside ``applyFetchResults``
      // (the F-5 source-drift pin guards the prod counterpart).
      this._liveMissionsPayload.set(liveMissions);
    }
    if (recentMissions !== null && !recentMissions.degraded) {
      this._recentMissionsPayload.set(recentMissions);
    }
    const liveMissionsDegraded = liveMissions !== null && liveMissions.degraded;
    const recentMissionsDegraded = recentMissions !== null && recentMissions.degraded;
    if (liveMissionsDegraded) this.onLegError('liveMissions', 'degraded envelope');
    if (recentMissionsDegraded) this.onLegError('recentMissions', 'degraded envelope');
    const anyNull =
      active === null ||
      recent === null ||
      liveMissions === null ||
      recentMissions === null ||
      deferBlocked === null ||
      liveMissionsDegraded ||
      recentMissionsDegraded;
    if (!anyNull) this.lastIntakeError.set(null);
    if (
      active !== null ||
      recent !== null ||
      (liveMissions !== null && !liveMissions.degraded) ||
      (recentMissions !== null && !recentMissions.degraded) ||
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
      component.applyFetchResult([], [], twoPayload, twoPayload, null);
      expect(component.liveMissionCount()).toBe(2);
      expect(component.missionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');

      // Now the BE returns a degraded envelope on the count leg —
      // empty rows, null total, degraded:true. The intake MUST NOT
      // write the count leg's signal. The list leg is still healthy
      // so the panel's payload retains.
      const degradedPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      component.applyFetchResult([], [], degradedPayload, twoPayload, null);
      // Last good data retained across the degraded count tick.
      expect(component.liveMissionCount()).toBe(2);
      expect(component.missionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      // The raw recent-payload signal was NOT overwritten either —
      // the canonical helper's null branch keeps the missions
      // projection honest. A subsequent healthy tick re-syncs both.
      const payload = component.lastRecentMissionsPayload();
      expect(payload).not.toBeNull();
      expect(payload!.degraded).toBe(false);

      // The next healthy tick DOES update both signals.
      const zeroPayload = MockJobQueueIndicatorComponent.buildMissionsPayload([], 0);
      component.applyFetchResult([], [], zeroPayload, zeroPayload, null);
      expect(component.liveMissionCount()).toBe(0);
      expect(component.missionsList().length).toBe(0);
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
      component.applyFetchResult([], [], degradedPayload, twoPayload, null);
      expect(component.lastIntakeError()).toBe('liveMissions: degraded envelope');
      // C2/C3 retention stays EXACTLY as-is across the same tick.
      expect(component.liveMissionCount()).toBe(2);
      expect(component.missionsList().length).toBe(2);
      expect(component.displayText()).toBe('missions: 2');
      expect(component.lastRecentMissionsPayload()!.degraded).toBe(false);
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

      it('binds [missions]="missionsList()" so the panel sees the LEG A + LEG B composition (no third source)', () => {
        // F-5 structural pin: the panel's [missions] input must come
        // from the indicator's ``missionsList()`` (the LEG A + LEG B
        // composition computed). An F-5 revert that re-binds directly
        // to a single leg (or to the legacy ``missionsPayload()``
        // signal) would slip past every behavioural test (the
        // composition would be the same if only one leg had rows)
        // but the template's wiring would be wrong.
        expect(templateHtml).toContain('[missions]="missionsList()"');
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

      it('LEG B (recent) still uses listMissions({ limit: 20 })', () => {
        // LEG B feeds the panel's terminal mission nodes + recentFlat
        // via the unfiltered ``missions`` page. The list-page limit
        // must stay at 20 (the brief's panel cap) so the panel
        // renders consistently.
        expect(componentTs).toContain('listMissions({ limit: 20 })');
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

  describe('missionsList signal — feeds the panel', () => {
    it('exposes the latest successful missions payload', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
        { mission_id: 'm-2', liveness: 'paused' },
      ]);
      component.applyFetchResult([], [], payload, payload, null);
      expect(component.missionsList().length).toBe(2);
      expect(component.missionsList().map((m) => m.mission_id).sort()).toEqual(['m-1', 'm-2']);
    });

    it('retains the last known missions across a degraded poll', () => {
      const payload = MockJobQueueIndicatorComponent.buildMissionsPayload([
        { mission_id: 'm-1', liveness: 'processing' },
      ]);
      component.applyFetchResult([], [], payload, payload, null);
      component.applyFetchResult([], [], null, null, null);
      expect(component.missionsList().length).toBe(1);
      expect(component.missionsList()[0].mission_id).toBe('m-1');
    });
  });

  // ── F-5 mission-tree single-source pin (2026-09-08) ──────────────────────
  //
  // F-5 closed: the badge's live-mission count came from the
  // filter-aware LEG A (limit=20, total=N live), but the tooltip's
  // per-liveness breakdown AND the panel's LIVE MISSIONS rows
  // derived from the unfiltered LEG B (limit=20, top-20 by
  // last_activity). When the unfiltered page's top-20 happened to be
  // all-terminal, the badge read "7" while the live section was empty
  // AND the breakdown said "(processing 0, pending 0, paused 0)" — a
  // structural contradiction (tester-dataset repro).
  //
  // The fix unifies the live consumers on ONE source (LEG A):
  // ``listMissions({ liveness: 'processing,pending,paused', limit: 20 })``
  // — the filter-aware page. Every live row the badge counts MUST
  // appear in the live section AND tally into the breakdown. LEG B
  // (the unfiltered page) feeds only the panel's RECENT terminal
  // mission nodes + recentFlat; live rows that appear in LEG B are
  // filtered out before they reach the panel, so they cannot bleed
  // into the LIVE MISSIONS section. The two sets are disjoint by
  // construction: leg A is live-only and filtered leg B is
  // terminal-only.

  describe('F-5: live consumers (count + live rows + breakdown) all read from LEG A — no contradiction possible', () => {
    it('badge shows the LEG A total (live=7) even when LEG B total is much larger (total=82)', () => {
      // F-1 carryover repro: live count = 7 (filter-aware total from
      // LEG A), LEG B total = 82 (unfiltered page total including
      // terminal missions). The badge MUST use LEG A — never LEG B —
      // so the visible number stays honest.
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
      // The recent leg's payload is much larger — it includes terminal
      // (completed/failed/cancelled) missions that the live filter
      // excluded. The first 7 rows happen to be live (BE orders by
      // last_activity_at desc), which is exactly the F-5 bug-class
      // scenario where the OLD wiring would have let LEG B's live rows
      // leak into the panel's live section + the breakdown.
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 82 }, (_, i) => ({
          mission_id: `m-${i}`,
          liveness: i < 7 ? ('processing' as const) : ('completed' as const),
        })),
        82
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Badge shows 7 (LEG A), NOT 82 (LEG B).
      expect(component.liveMissionCount()).toBe(7);
      expect(component.missionsSegmentText()).toBe('7');
      // Breakdown reads from LEG A only — 4 processing, 2 paused, 1
      // pending. (LEG B's 7 live rows are FILTERED OUT before reaching
      // the panel's [missions] input AND the breakdown, so LEG B's 7
      // processing rows never contribute.)
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(4);
      expect(bd['paused']).toBe(2);
      expect(bd['pending']).toBe(1);
      // Panel's [missions] input = LEG A (7 live) + LEG B filtered to
      // terminal (75 completed) = 82 rows total. ``buildQueueTree``
      // will route LEG A's 7 to ``tree.liveMissions`` and the 75
      // terminal ones to ``tree.recent`` — disjoint by construction.
      expect(component.missionsList().length).toBe(82);
      // Sanity pin: LEG A's payload is preserved as-is.
      expect(component.lastLiveMissionsPayload()!.total).toBe(7);
      expect(component.lastRecentMissionsPayload()!.total).toBe(82);
    });

    it('live count is correct when live missions > 20 (count comes from filtered total, NOT the 20-item page)', () => {
      // 25 live missions. The backend clamps to ``limit:20`` so LEG A
      // returns its top 20 rows (with ``total:25``); the badge's
      // count reads ``total`` so it shows 25, NOT 20 (the page size)
      // and NOT a misleading 75 from LEG B.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 20 }, (_, i) => ({
          mission_id: `live-${i}`,
          liveness: 'processing' as const,
        })),
        25
      );
      // The recent leg returns its first 20 — ``total`` is the
      // unfiltered total (would have falsely inflated the badge under
      // the OLD wiring). All 20 are processing so LEG B filtered to
      // terminal = 0.
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 20 }, (_, i) => ({
          mission_id: `m-${i}`,
          liveness: 'processing' as const,
        })),
        75
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Badge shows 25 (LEG A's ``total``), NOT 75 (LEG B's total),
      // NOT 20 (LEG A's page size).
      expect(component.liveMissionCount()).toBe(25);
      expect(component.missionsSegmentText()).toBe('25');
      // Breakdown covers the fetched 20 of LEG A — the cosmetic S4
      // ceiling (live > 20): count uses LEG A's ``total`` (=25) so
      // the badge stays honest; breakdown covers LEG A's fetched 20
      // rows (the page ceiling). Per the brief, this is acceptable;
      // only adjust tooltip wording if trivial.
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(20);
    });

    it('LEG A degraded envelope retains the LAST good live count (no false bare 0/0)', () => {
      // Seed a healthy live payload.
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], onePayload, onePayload, null);
      expect(component.liveMissionCount()).toBe(1);
      // Now LEG A degrades (LEG B is still healthy so the panel
      // keeps its terminal content).
      const degradedLive = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      const healthyRecent = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], degradedLive, healthyRecent, null);
      // The flag is set (LEG A degraded) AND the count signal
      // retains the last good value.
      expect(component.lastIntakeError()).toBe('liveMissions: degraded envelope');
      expect(component.liveMissionCount()).toBe(1);
    });

    it('LEG A catchError failure does NOT kill LEG B (leg independence)', () => {
      // Healthy LEG B first so the panel has rows to retain.
      // Both legs start with the same 1-row payload so the panel's
      // composition is well-defined (LEG A = 1 live, LEG B filtered
      // to terminal = 0 → 1 row total).
      const healthyRecent = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], healthyRecent, healthyRecent, null);
      expect(component.missionsList().length).toBe(1);
      // Now LEG A fails (per-leg catchError fires FIRST — sets
      // ``lastIntakeError`` — and the forkJoin next-handler receives
      // ``liveMissions: null``). LEG B is still healthy so it UPDATES
      // the panel's terminal payload (LEG B uses TERMINAL liveness
      // here so the filter-to-terminal pass lets them reach the
      // panel's RECENT section — proves LEG B updated independently).
      component.onLegError('liveMissions', new Error('count 500'));
      const updatedRecent = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'done-1', liveness: 'completed' },
          { mission_id: 'done-2', liveness: 'failed' },
        ],
        2
      );
      component.applyFetchResult([], [], null, updatedRecent, null);
      // The flag remains set from the catchError call — LEG A's
      // error is recorded.
      expect(component.lastIntakeError()).toBe('liveMissions: count 500');
      // LEG B updated — the panel's terminal content reflects the
      // new, larger payload (2 terminal missions) PLUS LEG A's
      // retained 1 live row → 3 rows total (LEG A 1 + LEG B
      // filtered-to-terminal 2).
      expect(component.missionsList().length).toBe(3);
    });

    it('LEG B degraded envelope retains the LAST good list (panel never flashes empty)', () => {
      const onePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], onePayload, onePayload, null);
      expect(component.missionsList().length).toBe(1);
      // Now LEG B degrades while LEG A is still healthy.
      const degradedRecent = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [],
        undefined,
        { degraded: true }
      );
      component.applyFetchResult([], [], onePayload, degradedRecent, null);
      expect(component.lastIntakeError()).toBe('recentMissions: degraded envelope');
      // The list payload retains the last good value — the panel
      // never flashes empty.
      expect(component.missionsList().length).toBe(1);
    });

    it('LEG B catchError failure does NOT kill LEG A (leg independence)', () => {
      // Seed a healthy live payload.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        1
      );
      component.applyFetchResult([], [], livePayload, livePayload, null);
      expect(component.liveMissionCount()).toBe(1);
      // Now LEG B fails (per-leg catchError fires FIRST — sets
      // ``lastIntakeError`` — and the forkJoin next-handler receives
      // ``recentMissions: null``). LEG A is still healthy so it
      // UPDATES the badge's N.
      component.onLegError('recentMissions', new Error('list 500'));
      const updatedLive = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [{ mission_id: 'live-1', liveness: 'processing' }],
        3
      );
      component.applyFetchResult([], [], updatedLive, null, null);
      // The flag remains set from the catchError call — LEG B's
      // error is recorded.
      expect(component.lastIntakeError()).toBe('recentMissions: list 500');
      // LEG A updated — the badge's N reflects the new count.
      expect(component.liveMissionCount()).toBe(3);
    });
  });

  // ── F-5 mandatory pins (2026-09-08, mission-tree single-source) ──────
  //
  // Three pins prove the F-5 wiring is structurally impossible to
  // bypass. Each is a separate describe so a regression lands on the
  // exact failure mode:
  //
  //   * single-source pin — live-section rows + tooltip breakdown +
  //     header count all sourced from LEG A. A revert that lets ANY
  //     consumer read LEG B (or the old limit:1 shape) flips one of
  //     these.
  //   * tester-dataset pin — unfiltered top-20 all-terminal + 7 live
  //     missions → LIVE MISSIONS shows all 7, tooltip shows the REAL
  //     split (not the broken "0/0/0"). The exact tester-dataset
  //     repro that surfaced the bug.
  //   * structural-disjointness pin — live rows from LEG B are
  //     filtered out before the panel sees them, so they cannot bleed
  //     into the LIVE MISSIONS section even when LEG B's top-N are
  //     live.

  describe('F-5 single-source pin: count + live rows + breakdown all read from LEG A', () => {
    it('badge count reads from LEG A — header N is total ?? missions.length of the live page', () => {
      // LEG A = 7 live (total=7), LEG B = 82 rows (75 terminal + 7
      // live that appear in the top-20). The badge MUST read 7 from
      // LEG A's total — never from LEG B's rows or total.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 7 }, (_, i) => ({
          mission_id: `live-${i}`,
          liveness: 'processing' as MissionLiveness,
        })),
        7
      );
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 82 }, (_, i) => ({
          mission_id: `m-${i}`,
          liveness: i < 7 ? ('processing' as const) : ('completed' as const),
        })),
        82
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Single-source assertion #1: badge N = LEG A's total (7), NOT
      // LEG B's total (82) or LEG B's row count (82).
      expect(component.liveMissionCount()).toBe(7);
      expect(component.missionsSegmentText()).toBe('7');
      // Sanity: LEG A's payload is the one being read for the count.
      expect(component.lastLiveMissionsPayload()!.total).toBe(7);
    });

    it('liveMissionBreakdown reads from LEG A — NOT from the panel\'s combined missionsList()', () => {
      // LEG A = 3 processing + 2 paused (5 total live). LEG B's top
      // page contains 2 LIVE rows (which are filtered out before the
      // panel sees them) + 18 terminal rows. The breakdown must
      // count the LEG A split (3 processing + 2 paused), NOT the
      // combined total (which would be 5 processing + 2 paused — 7,
      // a different number).
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
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          // 2 LIVE rows at the top of LEG B (would be the F-5 bug
          // class — they used to leak into the live section).
          { mission_id: 'leak-1', liveness: 'processing' },
          { mission_id: 'leak-2', liveness: 'paused' },
          // 18 terminal rows.
          ...Array.from({ length: 18 }, (_, i) => ({
            mission_id: `done-${i}`,
            liveness: 'completed' as MissionLiveness,
          })),
        ],
        20
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Single-source assertion #2: breakdown reflects LEG A only
      // (3 processing + 2 paused = 5). LEG B's 2 live rows do NOT
      // contribute — even though the panel's combined missionsList
      // would include them if they were not filtered out.
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(3);
      expect(bd['paused']).toBe(2);
      expect(bd['pending']).toBe(0);
      expect(bd['processing'] + bd['paused'] + bd['pending']).toBe(5);
      // Sanity: the LEG A payload is what's being read.
      expect(component.liveMissionsList().length).toBe(5);
    });

    it('panel\'s [missions] input is the LEG A + LEG B-filtered composition — disjoint by construction', () => {
      // LEG A = 5 live rows. LEG B = 5 LIVE + 15 terminal rows.
      // Panel's [missions] = 5 (LEG A) + 15 (LEG B filtered to
      // terminal) = 20 rows total. LEG B's 5 LIVE rows MUST NOT
      // appear in the panel's live count.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 5 }, (_, i) => ({
          mission_id: `live-${i}`,
          liveness: 'processing' as MissionLiveness,
        })),
        5
      );
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          ...Array.from({ length: 5 }, (_, i) => ({
            mission_id: `leak-${i}`,
            liveness: 'processing' as MissionLiveness,
          })),
          ...Array.from({ length: 15 }, (_, i) => ({
            mission_id: `done-${i}`,
            liveness: 'completed' as MissionLiveness,
          })),
        ],
        20
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Panel composition = LEG A (5) + LEG B filtered to terminal
      // (15) = 20.
      expect(component.missionsList().length).toBe(20);
      // None of LEG B's "leak-N" rows reach the panel's combined
      // input — they were filtered out by the live-only filter on
      // LEG B's contribution to the panel.
      const leakIds = component.missionsList()
        .map((m) => m.mission_id)
        .filter((id) => id?.startsWith('leak-'));
      expect(leakIds.length).toBe(0);
      // All LEG A's "live-N" rows DO reach the panel.
      const liveIds = component.missionsList()
        .map((m) => m.mission_id)
        .filter((id) => id?.startsWith('live-'));
      expect(liveIds.length).toBe(5);
    });
  });

  describe('F-5 tester-dataset pin: 7 live + unfiltered top-20 all-terminal → live section non-empty + correct tooltip', () => {
    it('live section + breakdown + header count are all consistent when LEG B is all-terminal', () => {
      // Tester-dataset repro: the unfiltered top-20 (LEG B) is
      // entirely terminal — this is the exact payload shape that
      // triggered the bug. LEG A has 7 live with a real mix:
      // processing 3, pending 2, paused 2.
      //
      // Pre-fix behaviour (the bug): badge = 7, tooltip =
      // "7 (processing 0, pending 0, paused 0)", live section
      // EMPTY. The breakdown was reading from LEG B's empty-live
      // rows, contradicting the header count.
      //
      // Post-fix behaviour: badge = 7, tooltip =
      // "7 (processing 3, pending 2, paused 2)", live section
      // NON-EMPTY (7 rows from LEG A).
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
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        Array.from({ length: 20 }, (_, i) => ({
          mission_id: `done-${i}`,
          liveness: 'completed' as MissionLiveness,
        })),
        20
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Header: badge shows 7 (LEG A total), NOT 20 (LEG B total).
      expect(component.liveMissionCount()).toBe(7);
      expect(component.missionsSegmentText()).toBe('7');
      // Tooltip: real split from LEG A (3 + 2 + 2), NOT the broken
      // "0 + 0 + 0" the pre-fix code produced.
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(3);
      expect(bd['pending']).toBe(2);
      expect(bd['paused']).toBe(2);
      const tt = component.tooltipText();
      expect(tt).toContain('Live missions: 7 (processing 3, pending 2, paused 2)');
      // Live section: 7 live rows from LEG A, NON-EMPTY.
      const liveSection = component.missionsList().filter((m) =>
        m.liveness === 'processing' || m.liveness === 'pending' || m.liveness === 'paused'
      );
      expect(liveSection.length).toBe(7);
      // Panel composition: LEG A (7 live) + LEG B filtered to
      // terminal (20 completed) = 27 rows total.
      expect(component.missionsList().length).toBe(27);
    });
  });

  describe('F-5 structural-disjointness pin: LEG B live rows are filtered out before the panel sees them', () => {
    it('even when LEG B is ALL live rows, the panel\'s live section stays sourced from LEG A only', () => {
      // Adversarial payload: LEG B returns 20 LIVE rows (no
      // terminal). The F-5 fix must filter them all out so the
      // panel's live count and breakdown stay sourced from LEG A.
      // LEG A's total is 7 (3 processing + 2 pending + 2 paused);
      // LEG B's 20 rows are 10 processing + 5 pending + 5 paused.
      // Pre-fix: live section would show 27 rows and breakdown
      // would say 13 processing + 7 pending + 7 paused — wildly
      // out of sync with the badge's 7. Post-fix: live section =
      // 7 rows (LEG A only) and breakdown matches LEG A.
      const livePayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          { mission_id: 'a-1', liveness: 'processing' },
          { mission_id: 'a-2', liveness: 'processing' },
          { mission_id: 'a-3', liveness: 'processing' },
          { mission_id: 'a-4', liveness: 'pending' },
          { mission_id: 'a-5', liveness: 'pending' },
          { mission_id: 'a-6', liveness: 'paused' },
          { mission_id: 'a-7', liveness: 'paused' },
        ],
        7
      );
      const recentPayload = MockJobQueueIndicatorComponent.buildMissionsPayload(
        [
          ...Array.from({ length: 10 }, (_, i) => ({
            mission_id: `b-${i}`,
            liveness: 'processing' as MissionLiveness,
          })),
          ...Array.from({ length: 5 }, (_, i) => ({
            mission_id: `b-${10 + i}`,
            liveness: 'pending' as MissionLiveness,
          })),
          ...Array.from({ length: 5 }, (_, i) => ({
            mission_id: `b-${15 + i}`,
            liveness: 'paused' as MissionLiveness,
          })),
        ],
        20
      );
      component.applyFetchResult([], [], livePayload, recentPayload, null);
      // Badge: 7 (LEG A total). NOT 27 (LEG A rows + LEG B live).
      expect(component.liveMissionCount()).toBe(7);
      // Breakdown: LEG A only. NOT 13 + 7 + 7 = 27.
      const bd = component.liveMissionBreakdown();
      expect(bd['processing']).toBe(3);
      expect(bd['pending']).toBe(2);
      expect(bd['paused']).toBe(2);
      // Panel composition: LEG A (7 live) + LEG B filtered to
      // terminal (0, since LEG B had no terminal) = 7 rows total.
      // The 20 LEG B LIVE rows are dropped — they never reach the
      // panel.
      expect(component.missionsList().length).toBe(7);
      // All rows in the panel start with "a-" (LEG A), NOT "b-".
      const panelIds = component.missionsList().map((m) => m.mission_id);
      expect(panelIds.every((id) => id?.startsWith('a-'))).toBe(true);
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
