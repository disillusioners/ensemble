import {
  Component,
  DestroyRef,
  inject,
  signal,
  computed,
  OnInit,
  OnDestroy,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule, MatMenuTrigger } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar } from '@angular/material/snack-bar';
import { JobService } from '../../services/job.service';
import { ProjectService } from '../../services/project.service';
import { TabStateService } from '../../services/tab-state.service';
import { Job, JobStatus, MissionLiveness, isTerminalStatus, isLiveMissionLiveness } from '../../models/job.model';
import { MissionListResponse, MissionSummary, missionCountFromListResponse } from '../../models/mission.model';
import { DeferBlockedStatus, DeferBlockIndicator, DeferBlockSeverity, DeferBlockAction, deferBlockIndicator, deferBlockAction } from '../../models/defer-blocked.model';
import { forkJoin, catchError, of } from 'rxjs';
import { JobQueuePanelComponent } from '../job-queue-panel/job-queue-panel.component';

/**
 * Header status indicator that surfaces the live job queue as a
 * SEGMENTED status pill: jobs (running/non-terminal) on the left,
 * live missions (count + pulse dot) on the right, defer-gate warning
 * ⚠ adjacent (unchanged). The whole pill is ONE click target that
 * opens the mat-menu — affordances are NOT split.
 *
 * The button is ``mat-button`` (not icon-button) so the count can
 * render as plain monospace text in the header bar. Clicking opens
 * the menu; clicking a row inside the panel triggers navigation
 * to the underlying instance via ``onJobClick``.
 *
 * Data sources (all on ONE 8s tick — no separate pollers):
 *   - ``JobService.listActiveJobs()``             — running + pending jobs
 *   - ``JobService.listRecentJobs(10)``           — terminal jobs for the
 *     ``Recent`` section of the embedded panel
 *   - ``JobService.listMissions({ liveness: 'processing,pending,paused',
 *     limit: 20 })``  — leg A (live): the live-only missions page that
 *     feeds the badge count, the LIVE MISSIONS panel rows, AND the
 *     tooltip's per-liveness breakdown. Single source of truth for all
 *     three live consumers — the F-5 contradiction class becomes
 *     structurally impossible (every live row the badge counts MUST
 *     appear in the live section AND tally into the breakdown).
 *   - ``JobService.listMissions({ limit: 20 })``  — leg B (recent): the
 *     unfiltered missions page that feeds the panel's RECENT terminal
 *     mission nodes + recentFlat ONLY. Live rows that appear in leg B
 *     are filtered out before they reach the panel, so they cannot
 *     bleed into the live section.
 *   - ``JobService.listDeferBlocked()``           — defer-gate warning
 *     payload for the severity icon beside the badge
 *
 * The former ``JobService.listLiveMissionCount(limit=1)`` round-trip
 * is REPLACED — the segmented pill needs the per-liveness breakdown
 * for its tooltip AND the panel's live rows, so a single
 * ``listMissions({ liveness: 'processing,pending,paused', limit: 20 })``
 * call serves all three live consumers.
 *
 * All four fire together via ``forkJoin`` on the same 8s tick so the
 * snapshot stays internally consistent. The two additive participants
 * (missions + deferBlocked) carry their own ``catchError`` so a
 * failure (404/503 during BE rollout skew, 500, network) degrades
 * THAT participant to ``null`` without failing the whole ``forkJoin``
 * — the jobs intake keeps flowing. ``null`` missions ⇒ the last known
 * data is RETAINED (never a bare 0/0). ``null`` deferBlocked ⇒ the
 * warning icon hides.
 *
 * Project names are resolved once on init via
 * ``ProjectService.listProjects()`` and cached in ``projectNameMap``
 * for the lifetime of the component.
 *
 * Lifecycle: all subscriptions are tied to ``DestroyRef`` via
 * ``takeUntilDestroyed`` so polling stops when the component is
 * torn down — the indicator lives in the header which may be
 * destroyed during navigation.
 */
@Component({
  selector: 'app-job-queue-indicator',
  standalone: true,
  imports: [
    CommonModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    MatTooltipModule,
    JobQueuePanelComponent,
  ],
  templateUrl: './job-queue-indicator.component.html',
  styleUrl: './job-queue-indicator.component.scss'
})
export class JobQueueIndicatorComponent implements OnInit, OnDestroy {
  private readonly jobService = inject(JobService);
  private readonly projectService = inject(ProjectService);
  private readonly tabStateService = inject(TabStateService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly snackBar = inject(MatSnackBar);

  /** Poll interval, in milliseconds. */
  private readonly POLL_INTERVAL_MS = 8000;

  /** Raw active jobs (running + paused + pending) returned by listActiveJobs.
   *  Public surface: the panel input (``[activeJobs]``) reads the full
   *  non-terminal set so queued jobs reach ``tree().queued`` instead of
   *  being silently dropped by the prior running-only filter (C1 fix). */
  readonly activeJobs = signal<Job[]>([]);

  /**
   * Raw recent jobs returned by ``listRecentJobs(10)`` — defensive
   * for now, but downstream consumers should always read
   * ``recentJobs`` (the public computed) so non-terminal statuses
   * are filtered out.
   */
  private readonly allRecentJobs = signal<Job[]>([]);

  /**
   * Public recent jobs — terminal-only subset of ``allRecentJobs``,
   * deterministically sorted (newest ``completed_at`` first,
   * falling back to ``created_at``) and capped at 10. Using
   * ``isTerminalStatus`` here keeps the public surface safe even
   * if the backend ever leaks a non-terminal status into the
   * recent endpoint.
   */
  readonly recentJobs = computed<Job[]>(() =>
    this.allRecentJobs()
      .filter((j) => isTerminalStatus(j.status))
      .sort((a, b) => {
        const aT = a.completed_at ?? a.created_at;
        const bT = b.completed_at ?? b.created_at;
        return bT.localeCompare(aT);
      })
      .slice(0, 10)
  );

  /**
   * Cached project_id → project name. Rebuilt on init. Keys are
   * strings (project ids) — the ``null`` key variant exists for
   * parity with the panel input type but is unused because the
   * ProjectService only returns real project ids.
   */
  readonly projectNameMap = signal<Map<string | null, string>>(new Map());

  /** Reference to the mat-menu trigger so we can programmatically close it. */
  @ViewChild(MatMenuTrigger) menuTrigger?: MatMenuTrigger;

  /** Running (processing/paused/active) job count — the X in "X/Y". */
  readonly runningCount = computed(
    () => this.activeJobs().filter((j) => isRunningStatus(j.status)).length
  );

  /** Pending (pending/queued) job count. */
  readonly pendingCount = computed(
    () => this.activeJobs().filter((j) => isPendingStatus(j.status)).length
  );

  /** Total non-terminal jobs (running + pending) — the Y in "X/Y". */
  readonly totalNonTerminal = computed(
    () => this.runningCount() + this.pendingCount()
  );

  /** Idle state — drives the muted styling on the button. */
  readonly isIdle = computed(() => this.totalNonTerminal() === 0);

  // ── Mission awareness — sourced from the authoritative projection ───

  /**
   * F-5 fix (2026-09-08) — LEG A (live) missions payload. Last
   * successful ``listMissions({ liveness: 'processing,pending,paused',
   * limit: 20 })`` response — the single source of truth for the
   * live consumers:
   *
   *   - badge's ``liveMissionCount`` (``total ?? missions.length``);
   *   - panel's LIVE MISSIONS rows (``liveMissionsList``);
   *   - tooltip's per-liveness breakdown (``liveMissionBreakdown``).
   *
   * Every consumer reads from this SAME list, so the F-5 bug class
   * (count leg says 7 but the panel's live section is empty because
   * the unfiltered leg's top-20 happens to be all-terminal) becomes
   * structurally impossible: a live row the badge counts MUST appear
   * in the live section AND tally into the breakdown.
   *
   * ``null`` when no fetch has landed yet OR the latest poll's live
   * leg degraded. The badge retains the previous good count across
   * degraded/null ticks so it never flips back to a false bare 0/0.
   * C2 fix: a 200-OK ``degraded:true`` envelope is NO LONGER written
   * here — it would clobber the last good payload and turn the badge
   * into a false bare 0/0.
   */
  private readonly liveMissionsPayload = signal<MissionListResponse | null>(null);

  /**
   * F-5 fix (2026-09-08) — LEG B (recent) missions payload. Last
   * successful ``listMissions({ limit: 20 })`` response — the
   * unfiltered page that feeds ONLY the panel's RECENT terminal
   * mission nodes + ``recentFlat``. Live rows that appear in this
   * page are FILTERED OUT before the panel sees them, so they can
   * never bleed into the LIVE MISSIONS section.
   *
   * The retention / degraded-flag / ``lastFetchAt`` semantics here
   * are independent of leg A: a degraded envelope on one leg does not
   * touch the other's last good payload, and each is reported via
   * ``recordLegError`` with its own leg name so the UI can flag
   * which projection degraded.
   */
  private readonly recentMissionsPayload = signal<MissionListResponse | null>(null);

  /**
   * Distinct live missions, from the missions projection this
   * component polls alongside its job intake (same 8s tick, no
   * separate poller).
   *
   * REPLACES the former receipt-window derivation
   * (``liveMissionIds(active + recent)``): settled tokens that never
   * reached the receipt intake made that N read 0 while a leader
   * mission was visibly working. ``/api/missions`` is correct and
   * authoritative — one mission per instance, liveness-filtered
   * server-side.
   *
   * C3 fix: widens to ``number | null`` and routes through the
   * canonical ``missionCountFromListResponse`` helper (mission.model.ts).
   * The helper returns ``null`` on a degraded envelope — we then
   * LEAVE THIS SIGNAL UNTOUCHED on degraded ticks so the value
   * retains the last good number. Initial state is ``null``
   * (pre-data; the display layer treats ``null`` as 0 for badge
   * formatting). On a healthy tick that legitimately reports zero
   * missions, the value is 0 (a real update, not a degraded gap).
   */
  private readonly liveMissionCountRaw = signal<number | null>(null);

  /**
   * Public live-mission count. Widened to ``number | null`` per C3
   * — ``null`` means we have never received a good count (pre-data
   * state). Once a good number arrives, this signal retains it
   * across degraded/null ticks so the badge never flips back to a
   * false bare 0/0.
   */
  readonly liveMissionCount = computed<number | null>(() => this.liveMissionCountRaw());

  /**
   * True when the missions projection reports at least one live
   * mission — i.e. we have a positive last-known count. ``null``
   * (pre-data) and 0 (healthy tick, no live missions) both read as
   * false; the badge then branches to ``'idle'`` / ``'segmented'``
   * based on the jobs side.
   */
  readonly hasLiveMissions = computed(() => {
    const n = this.liveMissionCount();
    return n !== null && n > 0;
  });

  /**
   * F-5 fix (2026-09-08) — LEG A's mission rows. The live-only list
   * (filter-aware via the backend's ``liveness`` param) feeds the
   * panel's LIVE MISSIONS rows AND the tooltip's per-liveness
   * breakdown. Reading from LEG A (not the panel's combined input)
   * is what closes the F-5 contradiction — the breakdown always
   * counts exactly what the badge counts.
   */
  readonly liveMissionsList = computed<MissionSummary[]>(() => {
    return this.liveMissionsPayload()?.missions ?? [];
  });

  /**
   * Per-liveness breakdown of LEG A's mission rows — drives the
   * segmented pill's tooltip ``Live missions: N (processing a,
   * pending b, paused c)`` line.
   *
   * F-5 fix: derives from ``liveMissionsList`` (LEG A) ONLY. A leg-B
   * unfiltered row that happens to be live is NOT counted here, even
   * though it reaches the panel — the breakdown's contract is
   * "counts what the badge counts" and the badge counts LEG A.
   */
  readonly liveMissionBreakdown = computed(() => {
    const list = this.liveMissionsList();
    let processing = 0;
    let pending = 0;
    let paused = 0;
    for (const m of list) {
      if (m.liveness === 'processing') processing += 1;
      else if (m.liveness === 'pending') pending += 1;
      else if (m.liveness === 'paused') paused += 1;
    }
    return { processing, pending, paused } as Record<MissionLiveness, number>;
  });

  /**
   * F-5 fix (2026-09-08) — panel's ``[missions]`` input. Composed of
   * TWO disjoint sets so ``buildQueueTree``'s liveness partition can
   * never route a leg-B row into the live section:
   *
   *   1. LEG A rows — every row has ``liveness`` in
   *      ``{processing, pending, paused}`` (BE filter applied), so
   *      ``buildQueueTree`` routes them all to ``tree.liveMissions``.
   *   2. LEG B rows, FILTERED to terminal liveness
   *      (``{completed, failed, cancelled}``) — live rows that
   *      appear in leg B's page are dropped here so they can never
   *      reach ``tree.liveMissions``. The remainder is routed to
   *      ``tree.recent`` (terminal mission nodes) or ``recentFlat``.
   *
   * No overlap is possible by construction: leg A is live-only and
   * the leg-B filter is terminal-only. The two sets are disjoint
   * even when the BE returns overlapping rows in the two responses.
   */
  readonly missionsList = computed<MissionSummary[]>(() => {
    const live = this.liveMissionsList();
    const recentPayload = this.recentMissionsPayload();
    const recentOnly = (recentPayload?.missions ?? []).filter(
      (m) =>
        m.liveness !== null &&
        !isLiveMissionLiveness(m.liveness as MissionLiveness)
    );
    return [...live, ...recentOnly];
  });

  /**
   * Defer-gate warning affordance, derived via the pure
   * ``deferBlockIndicator`` model helper. ``null`` = no render (zero
   * pending defer jobs, or the endpoint degraded/404 during rollout
   * skew — the icon hides silently).
   */
  readonly deferBlockWarning = signal<DeferBlockIndicator | null>(null);

  /**
   * Raw defer-blocked payload from the latest poll — the input the
   * WS4 holder-action derivation (``deferBlockAction``) needs. The
   * derived indicator carries only severity/tooltip; the actions
   * need the actionable holder's identity.
   */
  private readonly deferBlockedPayload = signal<DeferBlockedStatus | null>(null);

  /**
   * WS4 holder actions for the warning affordance — ``null`` = no
   * action offered (no payload, zero pending defer jobs, or no
   * instance-backed actionable holder). Pure derivation via the
   * model helper (house convention: components stay thin computeds
   * over model helpers).
   */
  readonly deferBlockActionTarget = computed<DeferBlockAction | null>(() =>
    deferBlockAction(this.deferBlockedPayload())
  );

  /** True while a holder action is in flight (buttons disabled). */
  readonly holderActionInProgress = signal(false);

  /** Material icon name per severity — presentation-only mapping. */
  private static readonly DEFER_BLOCK_ICONS: Record<DeferBlockSeverity, string> = {
    amber: 'warning',
    info: 'info',
    red: 'error',
  };

  /** Icon shown in the warning affordance ('' when hidden). */
  readonly deferBlockIcon = computed(() => {
    const warning = this.deferBlockWarning();
    return warning ? JobQueueIndicatorComponent.DEFER_BLOCK_ICONS[warning.severity] : '';
  });

  /**
   * Pill STATE — drives which template branch renders (segmented /
   * missions-only / idle). Three discrete branches keep the styling
   * clean and the spec straightforward; the underlying numbers flow
   * through ``runningCount`` / ``pendingCount`` / ``liveMissionCount``
   * computeds.
   *
   *   * ``'segmented'`` — jobs present AND live missions present.
   *     Left segment = running/non-terminal count, right = mission
   *     count + pulse dot. Both numbers stay explained.
   *   * ``'missions-only'`` — queue idle (no non-terminal jobs) BUT
   *     live missions exist. Pill shows the right segment only
   *     (pulse + count) — a working leader never reads as system idle.
   *   * ``'idle'`` — both empty. Muted grey.
   */
  readonly pillState = computed<'segmented' | 'missions-only' | 'idle'>(() => {
    if (this.totalNonTerminal() > 0) {
      return 'segmented';
    }
    if (this.hasLiveMissions()) return 'missions-only';
    return 'idle';
  });

  /**
   * Jobs segment text — left side of the segmented pill. Format is
   * ``X/Y`` where X = running, Y = total non-terminal (running +
   * pending). Matches the legacy ``displayText`` when jobs are
   * present.
   */
  readonly jobsSegmentText = computed(
    () => `${this.runningCount()}/${this.totalNonTerminal()}`
  );

  /**
   * Missions segment text — right side of the segmented pill. Just
   * the integer N (no ``missions: `` prefix anymore — the pill's
   * segmented layout already conveys "this is the missions count").
   * ``null`` (pre-data, never received a good value) renders as
   * ``0`` so the pill doesn't ship a literal "null" string.
   */
  readonly missionsSegmentText = computed(
    () => `${this.liveMissionCount() ?? 0}`
  );

  /**
   * Tooltip text shown on hover — multi-line breakdown per the brief:
   * ``Running X · Queued Y · Live missions: N (processing a,
   * pending b, paused c) · refreshed Ns ago``. Always present, even
   * when idle, so the user can distinguish a real idle from a
   * transient refresh gap.
   *
   * C3 fix: ``Live missions`` line is suffixed with ``(count
   * unavailable)`` when ``liveMissionCount`` is ``null`` (pre-data
   * state — never received a good value). Once we have a real
   * count, the line reads as ``N (processing a, ...)`` even across
   * degraded/null ticks (the signal retains the last good value).
   */
  readonly tooltipText = computed(() => {
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

  /** Seconds since the last successful poll — drives ``refreshed Ns ago``. */
  readonly refreshAgeSeconds = computed(() => {
    if (!this.lastFetchAt()) return 0;
    return Math.max(0, Math.floor((Date.now() - this.lastFetchAt()!) / 1000));
  });

  /** Wall-clock time of the last successful forkJoin — used by ``refreshAgeSeconds``. */
  private readonly lastFetchAt = signal<number | null>(null);

  /** Running-only subset (processing/paused/active) — drives the
   *  badge's runningCount + tooltip. NOT the panel input. */
  readonly runningJobs = computed(() =>
    this.activeJobs().filter((j) => isRunningStatus(j.status))
  );

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.loadProjectNames();
    this.fetchBadgeSignals();
    this.pollHandle = setInterval(() => this.fetchBadgeSignals(), this.POLL_INTERVAL_MS);
  }

  ngOnDestroy(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  /**
   * Fetch the project list once on init so we can map project_id →
   * name for the embedded panel. Failures are non-fatal: the map
   * stays empty and the panel falls back to shortened ids.
   */
  private loadProjectNames(): void {
    this.projectService.listProjects().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (response) => {
        const map = new Map<string | null, string>();
        for (const project of response.projects) {
          map.set(project.project_id, project.name);
        }
        this.projectNameMap.set(map);
      },
      error: (err) => {
        console.error('[JobQueueIndicator] Failed to load projects:', err);
      }
    });
  }

  /**
   * Per-leg error state — drives W-jobs-intake honesty (the "0/0,
   * refreshed Ns ago" impersonation gap). ``null`` when the latest
   * poll tick completed without per-leg failures; a non-null value
   * carries a short human-readable reason for the UI to surface (the
   * template's ``segment-missions`` segment gets a ``degraded``
   * modifier + an aria-label so screen readers don't read "0 live
   * missions" when the projection degraded).
   *
   * Set ONLY when a leg's per-participant catchError swallowed a
   * failure — the forkJoin outer error handler is a safety net for
   * operator-thrown values that escaped the per-leg isolation.
   *
   * F-5 (2026-09-08, mission-tree single-source pin) — the missions
   * leg is split into two parallel legs: ``liveMissions`` (filtered,
   * ``liveness:processing,pending,paused`` + ``limit:20``) and
   * ``recentMissions`` (unfiltered, ``limit:20``). Each carries its
   * own per-participant catchError so a failure in one does NOT
   * cascade into the other. Both legs feed ``recordLegError`` with
   * their respective leg name (``liveMissions`` / ``recentMissions``)
   * so the degraded flag honestly reflects which leg tripped — the
   * original contract's "any per-leg failure raises the flag" still
   * holds.
   */
  readonly lastIntakeError = signal<string | null>(null);

  /**
   * Fetch every header badge signal in ONE parallel ``forkJoin`` on
   * the same 8s tick — the name says "badge signals" because this is
   * THREE families, not just jobs (the pre-round-1 ``fetchJobs``
   * name understated it):
   *
   * - jobs intake — active + recent (``X/Y`` + the Recent section);
   * - ``liveMissions`` — LEG A (live)
   *   (``GET /api/missions?liveness=processing,pending,paused&limit=20``).
   *   The response's filter-aware ``total`` is the badge's live-mission
   *   count — authoritative when present. LEG A's ``missions`` array
   *   ALSO feeds the panel's LIVE MISSIONS rows AND the tooltip's
   *   per-liveness breakdown (single source of truth for all three
   *   live consumers — the F-5 contradiction class becomes
   *   structurally impossible: every row the badge counts MUST
   *   appear in the live section AND tally into the breakdown).
   *   ``limit:20`` (was ``limit:1`` under T2) keeps the live rows
   *   rich enough that the breakdown covers the visible 20 — for
   *   ``live > 20`` the breakdown covers the fetched 20 while the
   *   count uses ``total`` (acceptable; see S4-class note).
   * - ``recentMissions`` — LEG B (recent)
   *   (``GET /api/missions?limit=20`` — unfiltered page; the panel's
   *   ``tree().recent`` + ``tree().recentFlat`` derive from this AFTER
   *   the indicator strips any live rows that happen to appear in
   *   leg B). The split (F-5 fix) closes the "header ● 7, live
   *   section empty" self-contradiction: the live consumers all read
   *   from LEG A (the filter-aware leg), never from LEG B's
   *   unfiltered page.
   * - ``deferBlocked`` — defer-gate warning payload
   *   (``GET /api/queues/defer-blocked``).
   *
   * Every leg carries its own ``catchError`` (W-forkJoin legs) so a
   * failure (404/503 during BE rollout skew, 500, network) degrades
   * THAT leg to ``null`` without failing the whole ``forkJoin`` —
   * the healthy legs keep flowing.
   *
   * ``null`` liveMissions ⇒ the last known count is RETAINED (never
   * falsely idle); ``null`` recentMissions ⇒ the last known recent
   * list is RETAINED (the panel never flashes empty); ``null``
   * deferBlocked ⇒ the warning icon hides; ``null`` active/recent
   * ⇒ the last known job list is RETAINED (W-jobs-intake honesty —
   * a reset to ``[]`` plus a stale ``refreshed Ns ago`` would
   * impersonate a successful "0/0" poll). ``lastIntakeError`` flips
   * non-null on any per-leg error so the UI can flag the degradation
   * honestly.
   */
  private fetchBadgeSignals(): void {
    forkJoin({
      active: this.jobService.listActiveJobs().pipe(
        catchError((err) => {
          this.recordLegError('active', err);
          return of(null);
        })
      ),
      recent: this.jobService.listRecentJobs(10).pipe(
        catchError((err) => {
          this.recordLegError('recent', err);
          return of(null);
        })
      ),
      // F-5 fix — LEG A (live). Filter ``processing,pending,paused``
      // matches the canonical live value space; ``limit:20`` keeps
      // the panel rich enough to show a meaningful live section
      // while ``total`` survives the >20 ceiling for the badge
      // count. The response's ``total`` is filter-aware: when BE
      // honors the filter, ``total`` is the live count; when BE
      // can't honour the filter (degraded envelope), the envelope
      // returns ``degraded:true`` + ``total=null`` and the helper
      // returns ``null`` (count unavailable, retain last).
      liveMissions: this.jobService.listMissions({
        liveness: 'processing,pending,paused',
        limit: 20,
      }).pipe(
        catchError((err) => {
          this.recordLegError('liveMissions', err);
          return of(null);
        })
      ),
      // F-5 fix — LEG B (recent). Unfiltered, limit 20. Feeds the
      // panel's terminal mission nodes + recentFlat AFTER the
      // indicator filters out any live rows so they can never bleed
      // into the LIVE MISSIONS section. Per-liveness retention +
      // degraded-flag semantics unchanged from T2.
      recentMissions: this.jobService.listMissions({ limit: 20 }).pipe(
        catchError((err) => {
          this.recordLegError('recentMissions', err);
          return of(null);
        })
      ),
      deferBlocked: this.jobService.listDeferBlocked().pipe(
        catchError((err) => {
          this.recordLegError('deferBlocked', err);
          return of(null);
        })
      ),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ active, recent, liveMissions, recentMissions, deferBlocked }) =>
          this.applyFetchResults(active, recent, liveMissions, recentMissions, deferBlocked),
        // Safety net only — per-leg catchError above means this
        // path is unreachable for routine HTTP failures. It still
        // exists for synchronous throws from operator pipes that
        // escaped isolation.
        error: (err) => {
          console.error('[JobQueueIndicator] Forkjoin crashed:', err);
          this.recordLegError('forkjoin', err);
        }
      });
  }

  /**
   * Record a per-leg failure for W-jobs-intake honesty. Captures the
   * leg name + a short human-readable reason; the UI uses this to
   * flip the segment into a degraded visual + aria state instead of
   * impersonating a healthy "0/0" poll.
   */
  private recordLegError(leg: string, err: unknown): void {
    const reason =
      err instanceof Error ? err.message : typeof err === 'string' ? err : 'fetch failed';
    this.lastIntakeError.set(`${leg}: ${reason}`);
    console.warn(`[JobQueueIndicator] ${leg} leg degraded:`, err);
  }

  /**
   * Apply one poll tick's results to the component signals. Kept as
   * its own method so the logic-mirror spec can replicate it 1:1
   * with mocked service payloads.
   *
   * F-5 fix (2026-09-08, mission-tree single-source pin) — the
   * missions leg is split into two independent legs:
   *   * ``liveMissions`` — LEG A (live). Updates ``liveMissionCountRaw``
   *     via the canonical ``missionCountFromListResponse`` helper on
   *     a non-degraded tick; ``null`` on a degraded envelope (retain
   *     last good count, never falsely idle). The same payload's
   *     ``missions`` array also drives the panel's LIVE MISSIONS rows
   *     AND the tooltip's per-liveness breakdown — single source of
   *     truth for all three live consumers.
   *   * ``recentMissions`` — LEG B (recent). The unfiltered content
   *     page. Updates ``recentMissionsPayload`` on a non-degraded tick
   *     so the panel's RECENT terminal mission nodes + recentFlat
   *     derive from this AFTER the live-only rows are stripped
   *     (the panel's input composition lives in the ``missionsList``
   *     computed).
   *
   * Each leg is treated independently for retention / degraded-flag
   * / lastFetchAt-freeze purposes: a degraded envelope on one does
   * NOT clobber the other's last good payload, and vice versa.
   * Either leg's per-participant catchError or degraded-200 envelope
   * raises ``lastIntakeError`` so the UI honestly reports the
   * degradation.
   *
   * Other legs unchanged:
   * - jobs (active + recent) — ``null`` means the per-leg
   *   catchError swallowed a failure; we RETAIN the previous list
   *   rather than resetting to ``[]`` (W-jobs-intake honesty — a
   *   bare 0/0 plus a stale "refreshed Ns ago" would impersonate a
   *   healthy poll). ``lastIntakeError`` already records the leg
   *   failure for the UI to surface;
   * - ``deferBlocked === null`` hides the warning affordance;
   * - ``lastFetchAt`` only advances when AT LEAST ONE leg returned
   *   a usable payload (a degraded missions envelope counts as NOT
   *   returned) — a tick where every leg failed (or degraded)
   *   leaves the timestamp frozen so "refreshed Ns ago" doesn't lie
   *   about the staleness.
   */
  private applyFetchResults(
    active: Job[] | null,
    recent: Job[] | null,
    liveMissions: MissionListResponse | null,
    recentMissions: MissionListResponse | null,
    deferBlocked: DeferBlockedStatus | null
  ): void {
    if (active !== null) {
      this.activeJobs.set(active);
    }
    if (recent !== null) {
      this.allRecentJobs.set(recent);
    }
    // F-5 fix — LEG A drives ``liveMissionCountRaw``; LEG B drives
    // ``recentMissionsPayload``. Each is independently gated on
    // non-null + non-degraded so a degraded LEG A envelope cannot
    // clobber LEG B (and vice versa). The ``missionsList`` computed
    // composes the panel's input from BOTH signals; live rows
    // come from LEG A only, terminal rows come from LEG B filtered
    // to terminal liveness — disjoint by construction.
    if (liveMissions !== null && !liveMissions.degraded) {
      const count = missionCountFromListResponse(liveMissions);
      // ``count === null`` only when the envelope is degraded — already
      // filtered above. A healthy tick with ``total = 0`` returns 0
      // and IS recorded (legitimate update, not a degraded gap).
      this.liveMissionCountRaw.set(count);
    }
    if (recentMissions !== null && !recentMissions.degraded) {
      this.recentMissionsPayload.set(recentMissions);
    }
    // 200-OK ``degraded:true`` envelopes never route through the
    // per-leg ``catchError`` (HTTP succeeded), so flag each here —
    // same channel as the catchError paths — to flip the degraded
    // modifier + aria state honestly. Each leg is reported
    // independently so the UI can tell the operator which projection
    // degraded (the live count leg vs the recent content leg).
    const liveMissionsDegraded = liveMissions !== null && liveMissions.degraded;
    const recentMissionsDegraded = recentMissions !== null && recentMissions.degraded;
    if (liveMissionsDegraded) {
      this.recordLegError('liveMissions', 'degraded envelope');
    }
    if (recentMissionsDegraded) {
      this.recordLegError('recentMissions', 'degraded envelope');
    }
    // Clear the per-leg error flag only when ALL legs returned a
    // non-null payload (missions legs: non-degraded) — a partial-
    // failure or degraded tick keeps the flag set so the UI
    // continues to surface the degradation.
    const anyNull =
      active === null ||
      recent === null ||
      liveMissions === null ||
      recentMissions === null ||
      deferBlocked === null ||
      liveMissionsDegraded ||
      recentMissionsDegraded;
    if (!anyNull) {
      this.lastIntakeError.set(null);
    }
    // Stamp "last successful poll" only when at least one leg
    // returned data; an all-null (or all-degraded) tick leaves the
    // timestamp frozen.
    if (
      active !== null ||
      recent !== null ||
      (liveMissions !== null && !liveMissions.degraded) ||
      (recentMissions !== null && !recentMissions.degraded) ||
      deferBlocked !== null
    ) {
      this.lastFetchAt.set(Date.now());
    }
    this.deferBlockedPayload.set(deferBlocked);
    this.deferBlockWarning.set(
      deferBlocked === null ? null : deferBlockIndicator(deferBlocked)
    );
  }

  /**
   * WS4: force-complete the actionable stalled holder.
   *
   * The button is disabled unless the derived action says the holder
   * is ``stalled`` (mirrors-only) — the SERVER re-verifies via the
   * canonical probe at execution time, so a stale-UI click on a
   * since-gone-live holder is still refused safely (200 with
   * ``terminated=false``).
   */
  onForceCompleteHolder(): void {
    const target = this.deferBlockActionTarget();
    if (!target || !target.forceCompleteAllowed || this.holderActionInProgress()) {
      return;
    }
    this.holderActionInProgress.set(true);
    this.jobService
      .forceCompleteDeferHolder(target.holder.instance_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.holderActionInProgress.set(false);
          console.log(
            '[JobQueueIndicator] force-complete:',
            result.terminated ? 'terminated' : 'refused by server guard',
            result.message
          );
          this.snackBar.open(result.message, 'Close', {
            duration: 3000,
            panelClass: result.terminated ? 'success-snackbar' : 'error-snackbar',
          });
          this.fetchBadgeSignals();
        },
        error: (err) => {
          this.holderActionInProgress.set(false);
          console.error('[JobQueueIndicator] force-complete failed:', err);
        },
      });
  }

  /**
   * WS4: re-send the actionable holder's queued defer messages as
   * foreground jobs (cancel + re-enqueue server-side).
   */
  onResendDeferredForeground(): void {
    const target = this.deferBlockActionTarget();
    if (!target || this.holderActionInProgress()) {
      return;
    }
    this.holderActionInProgress.set(true);
    this.jobService
      .resendDeferredForeground(target.holder.instance_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.holderActionInProgress.set(false);
          console.log(
            '[JobQueueIndicator] resend-foreground:',
            result.cancelled_defer_jobs,
            'cancelled,',
            result.resend_results.filter((r) => r.job_id).length,
            're-sent'
          );
          this.snackBar.open(result.message, 'Close', {
            duration: 3000,
            panelClass: 'success-snackbar',
          });
          this.fetchBadgeSignals();
        },
        error: (err) => {
          this.holderActionInProgress.set(false);
          console.error('[JobQueueIndicator] resend-foreground failed:', err);
        },
      });
  }

  /**
   * Handle a job click from the embedded panel.
   *
   * Flow:
   *   1. Close the dropdown so the menu surface disappears before
   *      we mutate tab/route state.
   *   2. Open the project tab (resolved via ``projectNameMap``
   *      with a first-8-chars fallback) — or switch to the ``all``
   *      tab if the job is unassigned.
   *   3. Navigate to the specific instance when ``instance_id`` is
   *      truthy, otherwise to the project/all instances list with
   *      no null trailing segment.
   */
  onJobClick(job: Job): void {
    this.menuTrigger?.closeMenu();

    const projectKey = job.project_id || 'all';
    if (job.project_id) {
      const name =
        this.projectNameMap().get(job.project_id) ?? job.project_id.slice(0, 8);
      this.tabStateService.addTab({ project_id: job.project_id, name });
    } else {
      this.tabStateService.setActiveTab('all');
    }

    const navigateTo: (string | null)[] = job.instance_id
      ? ['/projects', projectKey, 'instances', job.instance_id]
      : ['/projects', projectKey, 'instances'];
    this.router.navigate(navigateTo);
  }

  /**
   * T1 (2026-09-07, mission-tree final gaps) — handle the panel
   * footer's "Open full queue →" activation. The panel stays
   * DUMB/presentational and emits ``footerClick``; this handler
   * closes the dropdown and routes to the dedicated Jobs page
   * (``/jobs`` — confirmed in app.routes.ts as the lazy-loaded
   * ``JobsComponent``). Same flow as ``onJobClick`` for closing the
   * menu: drop the surface FIRST, then mutate route state, so the
   * user sees the menu disappear before the page transition.
   */
  onFooterClick(): void {
    this.menuTrigger?.closeMenu();
    this.router.navigate(['/jobs']);
  }
}

/**
 * Defensive status predicates. ``processing``/``pending`` are the
 * canonical names from the ``JobStatus`` enum, but the backend's
 * internal lifecycle still uses ``active``/``queued`` in some
 * paths (and the active-jobs endpoint filters on the latter).
 * Accepting both keeps the indicator robust if the backend ever
 * leaks those names through.
 *
 * ``paused`` is treated as running here: the backend classifies
 * it as non-terminal and the Jobs UI surfaces paused rows in the
 * active queue. Counting it as "running" keeps the header badge
 * in sync with the underlying queue state.
 *
 * Terminal classification is NOT redefined here: the canonical
 * ``isTerminalStatus`` (models/job.model.ts) is imported directly —
 * the former module-private copy predated the M3 ``settled`` token
 * and silently misclassified settled receipts as non-terminal.
 */
function isRunningStatus(status: JobStatus): boolean {
  return (
    status === 'processing' ||
    status === 'paused' ||
    (status as string) === 'active'
  );
}

function isPendingStatus(status: JobStatus): boolean {
  return status === 'pending' || (status as string) === 'queued';
}
