import { Component, signal, computed, inject, OnInit, OnDestroy, effect, DOCUMENT } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ScrollingModule } from '@angular/cdk/scrolling';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { Subscription, switchMap, of, catchError, tap, firstValueFrom } from 'rxjs';
import { JobService } from '../../services/job.service';
import { JobSseService } from '../../services/job-sse.service';
import { ProjectService } from '../../services/project.service';
import { TabStateService } from '../../services/tab-state.service';
import { QueueService } from '../../services/queue.service';
import { ApiService } from '../../services/api.service';
import { WorkService } from '../../services/work.service';
import { JobCardComponent } from '../../components/job-card/job-card.component';
import { JobDetailDrawerComponent } from '../../components/job-detail-drawer/job-detail-drawer.component';
import { JobCreateDialogComponent, JobCreateDialogResult } from '../../components/job-create-dialog/job-create-dialog.component';
import { QueueListComponent } from '../../components/queue-list/queue-list.component';
import { SearchableSelectComponent } from '../../components';
import { SystemCleanupConfirmDialogComponent } from '../../components/system-cleanup-confirm-dialog/system-cleanup-confirm-dialog.component';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../components/confirm-dialog/confirm-dialog.component';
import { Job, JobStatus, JobSource, isTerminalStatus } from '../../models/job.model';
import { JobQueue } from '../../models/job-queue.model';
import { Project } from '../../models/project.model';
import { Agent } from '../../models';
import { CleanupPreflight } from '../../models/cleanup-preflight.model';
import { DeferBlockedStatus, deferBlockAction } from '../../models/defer-blocked.model';
import { JobsPageStore } from './jobs-page.store';
import {
  JobsViewMode,
  hasActiveJobsFilter,
} from '../../models/jobs-filter-state.model';
import {
  POLL_INTERVAL_MS,
  REFOCUS_DEBOUNCE_MS,
  PollGateInputs,
  shouldTick,
} from './jobs-poll.model';
import {
  MAX_RENDER_ROWS,
  WINDOW_BANNER_COPY,
  RenderGuardOutcome,
  WindowItem,
  renderGuard,
  toWindowItems,
} from './jobs-window.model';
import {
  JobsEmptyStateKind,
  classifyJobsEmptyState,
  emptyStateCopy,
  JobsEmptyStateCopy,
} from './jobs-empty-state.model';

/**
 * Top-level view mode for the Jobs page (Phase 4 — Virtual Job
 * Management Surface).
 *
 * * ``'queues'``   — the legacy "Queues" view: queue sidebar on the
 *   left, jobs filtered by selected queue on the right. Backed by
 *   ``JobService``.
 * * ``'all-work'`` — the unified work list: queue sidebar still
 *   visible but inactive, main pane shows ALL work records (jobs +
 *   turns + reports) backed by ``WorkService``. The kind chip on
 *   each card tells the user which backing table the row came from.
 *
 * P1 (jobs-page-improvement): the type now lives in
 * ``jobs-filter-state.model.ts`` (view_mode is a ``JobsFilterState``
 * key) and is re-exported above — both view modes are projections of
 * ONE ``JobsPageStore`` pipeline.
 */
export type { JobsViewMode };

@Component({
  selector: 'app-jobs',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ScrollingModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
    MatSidenavModule,
    MatSnackBarModule,
    MatDialogModule,
    MatTooltipModule,
    MatCheckboxModule,
    JobCardComponent,
    JobDetailDrawerComponent,
    QueueListComponent,
    SearchableSelectComponent
  ],
  templateUrl: './jobs.component.html',
  styleUrl: './jobs.component.scss'
})
export class JobsComponent implements OnInit, OnDestroy {
  private readonly router = inject(Router);
  private readonly jobService = inject(JobService);
  private readonly jobSseService = inject(JobSseService);
  private readonly projectService = inject(ProjectService);
  private readonly tabStateService = inject(TabStateService);
  private readonly queueService = inject(QueueService);
  private readonly api = inject(ApiService);
  private readonly workService = inject(WorkService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);
  /**
   * Phase 4 — used for the lightweight ``GET /api/jobs/cleanup/preflight``
   * preflight call that drives the red-glow + tooltip on the System
   * Cleanup button. We inject ``HttpClient`` directly because
   * ``ApiService`` exposes a fixed menu of endpoints and does not
   * currently include a generic GET helper for one-off read payloads.
   */
  private readonly http = inject(HttpClient);

  private readonly STORAGE_KEY = 'job-page-selected-project';
  private readonly VIEW_MODE_KEY = 'job-page-view-mode';
  private refreshInterval: ReturnType<typeof setInterval> | null = null;
  /** Phase 2 — pending refocus-refresh timer id (debounce storm mitigation). */
  private refocusTimer: ReturnType<typeof setTimeout> | null = null;
  private sseSubscription: Subscription | null = null;
  private projectRestored = false;

  /**
   * P1 (jobs-page-improvement) — THE single fetch + filter pipeline
   * behind BOTH view modes. The pre-P1 dual path (component-local
   * ``filteredJobs`` over the jobs dataset; ``worksAsJobs`` bypassing
   * filters over the work dataset) is DELETED — every view mode is a
   * projection of this store.
   *
   * The fetchers wire the store to the services; both legs propagate
   * errors so the store's per-leg retain-last-data contract holds.
   */
  private readonly store = new JobsPageStore({
    fetchJobs: (filters) => this.jobService.listJobs(filters),
    fetchWorks: (filters) => this.workService.getWork(filters),
  });

  // Dataset + fetch-state aliases — the template keeps reading the
  // same names, but the signals are OWNED by the store now (single
  // source of truth; the component holds no shadow copies).
  readonly jobs = this.store.jobs;
  readonly works = this.store.works;
  readonly loading = this.store.jobsLoading;
  readonly error = this.store.jobsError;
  readonly workLoading = this.store.worksLoading;
  readonly workError = this.store.worksError;

  // ── Phase 2 — banner + poll-gate derived state (aliases over the
  // store). Kept here as component-level computeds so the template
  // reads them like pre-Phase-2 locals; the policy (windowIsFull,
  // shouldTick) lives in the pure models.
  readonly windowRowCount = this.store.windowRowCount;
  readonly windowBanner = this.store.windowBanner;
  readonly windowDegraded = this.store.windowDegraded;
  readonly fetchInFlight = this.store.fetchInFlight;
  /** The unified, view-mode-aware error string (first non-null leg error). */
  readonly unifiedError = this.store.error;

  // P1 review watch-item (a): the WorkService's own `loading` signal
  // is shadowed by the store's `worksLoading` — both can be true at
  // once. P2 ties them to one source: the template binds ONLY to the
  // store's flag (above) so the two cannot surface contradictory
  // spinner states. The page never reads `WorkService.loading`
  // directly — the `workLoading` alias here goes to the store.
  // (Pre-Phase-2: `loading()` returned `jobsLoading()`; the
  // queues-view Refresh button kept both flags in sync. P2 collapses
  // both to the store. F-5 pin in jobs-page.bindings.pins.spec.)

  // ── Phase 2 — virtual-scroll source + render guard ───────────────────
  //
  // ``windowItems`` is the flattened (header|row) scroll source the
  // template renders. Phase 2 ships the ``row`` arm only; Phase 3
  // extends the list with ``header`` items without re-plumbing.
  //
  // ``renderGuardOutcome`` enforces the 1000-row hard cap at RENDER
  // time over the template-bound projected rows — the plan's
  // acceptance that growth between fetch and render cannot slip
  // through. The component's render tree branches on the discriminator
  // so silent slicing is structurally impossible.

  readonly windowItems = computed<readonly WindowItem[]>(() =>
    toWindowItems(this.store.filteredJobs()),
  );

  readonly renderGuardOutcome = computed<RenderGuardOutcome<WindowItem>>(() =>
    renderGuard(this.windowItems()),
  );

  /**
   * Rows to render — either the full windowItems list (ok branch) or
   * the kept slice (guarded branch). The template binds here so the
   * cap is enforced consistently.
   */
  readonly renderRows = computed<readonly WindowItem[]>(() => {
    const out = this.renderGuardOutcome();
    return out.kind === 'ok' ? out.rows : out.kept;
  });

  /**
   * Render-guard truncation notice — non-null only when the
   * projection is at/above the cap. The template renders an explicit
   * affordance above the virtual scroll list with this copy + a
   * "switch to Queues view" link.
   */
  readonly truncationNotice = computed<string | null>(() => {
    const out = this.renderGuardOutcome();
    return out.kind === 'guarded' ? out.notice : null;
  });

  readonly truncationHiddenCount = computed<number | null>(() => {
    const out = this.renderGuardOutcome();
    return out.kind === 'guarded' ? out.hidden : null;
  });

  // ── Phase 2 — empty-state classifier ──────────────────────────────────
  //
  // ``classifyJobsEmptyState`` is pure; the component just wires the
  // inputs (loading / degraded / hasRows / hasActiveFilters / viewMode).
  // The plan's skeleton rule: skeleton ONLY for the first fetch — a
  // background refresh during ``dataEmpty``/``filterEmpty``/``errored``
  // RETAINS the last good list, never flashes a skeleton.

  readonly emptyStateKind = computed<JobsEmptyStateKind>(() =>
    classifyJobsEmptyState({
      loading: this.fetchInFlight(),
      degraded: this.windowDegraded(),
      hasRows: this.store.filteredJobs().length > 0,
      hasActiveFilters: hasActiveJobsFilter(this.store.filterState()),
      viewMode: this.viewMode(),
    }),
  );

  readonly emptyStateCopy = computed<JobsEmptyStateCopy>(() =>
    emptyStateCopy(this.emptyStateKind(), this.viewMode()),
  );

  /** Skeleton fires ONLY for the first fetch (no data to retain). */
  readonly showLoadingSkeleton = computed<boolean>(
    () => this.emptyStateKind() === 'loading',
  );

  /** Empty-state card fires when the projection is empty. */
  readonly showEmptyState = computed<boolean>(() => {
    const kind = this.emptyStateKind();
    return (
      kind === 'dataEmpty' ||
      kind === 'filterEmpty' ||
      kind === 'errored'
    );
  });

  // ── Phase 2 — poll-gate + visibility state ───────────────────────────
  //
  // ``tabVisible`` mirrors ``document.visibilityState``. The poll tick
  // consults ``shouldTick(tabVisible, drawerOpen, modalOpen,
  // fetchInFlight)`` and skips the HTTP call when any pause condition
  // is set. ``tabVisible`` starts TRUE (the spec-friendly default;
  // the listener updates it on mount).

  private readonly doc = inject(DOCUMENT);
  readonly tabVisible = signal<boolean>(true);

  /** True while a page-owned modal dialog (create / cleanup / confirm) is open. */
  readonly modalOpen = signal<boolean>(false);

  // ── Phase 2 — expansion state keyed by job_id (survives recycle) ─────

  /**
   * Expansion state for ``cdk-virtual-scroll``-rendered cards. The
   * plan calls this out explicitly: a DOM-local ``signal(false)`` in
   * the card would reset on every virtual recycle, so the parent
   * owns the source of truth keyed by ``job_id``. The card's
   * ``expanded`` input + ``expandToggle`` output wire this through.
   */
  readonly expandedJobIds = signal<Set<string>>(new Set());

  /** Phase 2 — virtual-scroll item size in px (card min-height tuned). */
  protected readonly itemSize = 144;

  readonly agents = signal<Agent[]>([]);
  readonly selectedJob = signal<Job | null>(null);
  readonly drawerOpen = signal(false);
  readonly projects = this.projectService.projects;

  // Queue sidebar selection — derived from the single filter state
  // (pre-P1 this was a component-local signal MIRRORED into the
  // filters object; the mirror is gone, the state is the store's).
  readonly selectedQueueId = computed(() => this.store.filterState().queue_id);
  readonly selectedProjectId = computed(() => this.filters().project_id ?? null);

  // Filter state — THE store's filterState (single source of truth;
  // pre-P1 this was a component-local ``JobFilters`` signal).
  readonly filters = this.store.filterState;

  // DLQ signals
  readonly retryingAll = signal(false);
  readonly isDeadLetterFilterActive = computed(() => this.filters().status?.includes('dead_letter') ?? false);

  // System cleanup signal — true while the cleanupAllJobs request is
  // in-flight. Used to disable the System Cleanup button via
  // [disabled] in the template.
  readonly cleanupInProgress = signal(false);

  // Phase 4 — system-wide bad-state count surfaced from
  // ``GET /api/jobs/cleanup/preflight``. Drives the red-glow pulse
  // animation and contextual tooltip on the System Cleanup button.
  readonly badStateCount = signal<number>(0);
  readonly hasBadState = computed(() => this.badStateCount() > 0);

  // Phase 5 — system-wide zombie-instance count from the same
  // preflight endpoint. Parallel to ``badStateCount`` and surfaced in
  // the confirm dialog + snackbar so the operator knows how many
  // non-terminal instances (with no live work) the cleanup is about
  // to terminate.
  readonly zombieInstanceCount = signal<number>(0);
  readonly hasZombieInstances = computed(
    () => this.zombieInstanceCount() > 0
  );

  // WS4 — live-vs-reap split from the same preflight. The
  // ``live_instance_count`` / ``live_instance_ids`` are the
  // UNBLOCK-ROUND ITEM 11 (2026-09-06) TRUTH-SURVIVOR set:
  // non-terminal ∧ not-zombie ∧ no non-mirror ACTIVE/queued
  // JobItem. The round-2 shape (just ``non-terminal ∖ zombie``)
  // over-promised survival — a holder of a non-mirror ACTIVE
  // mission JobItem is a non-zombie per the WS4 mission lens,
  // but Bucket 2 cancels + cascades to terminate_instance, so
  // such a holder does NOT actually survive cleanup despite
  // appearing in the round-2 list. The BE preflight now narrows
  // the list via the post-filter
  // ``SQLModelInstanceRepository.has_real_active_or_queued_work``;
  // the dialog renders the bounded list of TRULY-retained
  // instances. The canonical cleanup-truth-split sentence
  // (``CLEANUP_TRUTH_SPLIT_COPY`` in
  // cleanup-preflight.model.ts) is rendered verbatim on the
  // dialog. ``defer_blocked_count`` is surfaced SEPARATELY
  // because cleanup does NOT cancel deferred messages (the
  // dialog says so via the defer note).
  readonly liveInstanceCount = signal<number>(0);
  readonly liveInstanceIds = signal<string[]>([]);
  readonly deferBlockedCount = signal<number>(0);
  // Unblock-round ITEM 12 (2026-09-06): ``defer_holder_kind`` is
  // NOT on the preflight wire — the preflight endpoint
  // ``GET /api/jobs/cleanup/preflight`` does NOT emit it. The
  // field is sourced from the SEPARATE
  // ``GET /api/queues/defer-blocked`` endpoint and populated by
  // this component when the JS snapshot is wired (see the
  // setter below — populated via ``deferBlockAction(deferStatus)``
  // composition). The TS interface in
  // ``cleanup-preflight.model.ts`` annotates the field as a
  // type-completeness convenience only.
  readonly deferHolderKind = signal<CleanupPreflight['defer_holder_kind']>(null);

  // Deleted jobs filter — derived from the single filter state.
  readonly showDeleted = computed(() => this.store.filterState().include_deleted);

  // View mode signal (Phase 4) — 'queues' (legacy) or 'all-work'
  // (unified list backed by /api/work). P1: a PROJECTION of the
  // store's filter state, not an independent signal. Persisted to
  // localStorage by the handlers so the user's preferred view
  // survives a page reload.
  readonly viewMode = computed<JobsViewMode>(() => this.store.filterState().view_mode);
  private viewModeRestored = false;

  // SSE connection status
  readonly isConnected = this.jobSseService.isConnected;
  readonly retryAttempt = this.jobSseService.retryAttempt;
  readonly isRetrying = this.jobSseService.isRetrying;
  readonly isFailed = this.jobSseService.isFailed;
  readonly connectionState = this.jobSseService.connectionState;

  // Computed map of project_id -> job_queue_paused
  readonly projectPauseMap = computed(() => {
    const map = new Map<string, boolean>();
    for (const project of this.projects()) {
      map.set(project.project_id, project.job_queue_paused);
    }
    return map;
  });

  // Get pause state for the currently selected project
  readonly isCurrentProjectPaused = computed(() => {
    const projectId = this.selectedProjectId();
    return projectId ? (this.projectPauseMap().get(projectId) ?? false) : false;
  });

  // Get pause state for a specific project
  readonly getProjectPaused = (projectId: string): boolean => {
    return this.projectPauseMap().get(projectId) ?? false;
  };

  // Queue name map for job cards (queue_id -> queue_name)
  readonly queueNameMap = computed(() => {
    const map = new Map<string, string>();
    const queues = this.queueService.queues();
    for (const queue of queues) {
      map.set(queue.queue_id, queue.queue_name);
    }
    return map;
  });

  // ── P1: THE ONE PIPELINE ────────────────────────────────────────────
  //
  // The pre-P1 dual path is DELETED from this file:
  //
  // * ``filteredJobs`` (computed over ``jobs()`` ONLY) — replaced by
  //   ``store.filteredJobs``, which filters the ACTIVE dataset.
  // * ``worksAsJobs()`` / ``workToJob()`` (all-work mapping that
  //   BYPASSED filters entirely and hard-nulled ``started_at`` /
  //   ``completed_at``) — replaced by the pure ``workToJob`` mapper
  //   in ``work.model.ts`` + ``store.filteredJobs``.
  // * ``displayedJobs``'s view-mode BRANCH — both modes now read the
  //   same store projection; the alias below is not a second path.

  /**
   * Source for the displayed list — the store's unified projection.
   * In the all-work view the store projects Work rows through the
   * row-parity ``workToJob`` mapper INSIDE the pipeline; the card
   * template stays type-stable on ``Job`` either way.
   */
  readonly displayedJobs = this.store.filteredJobs;

  readonly hasJobs = computed(() => this.store.filteredJobs().length > 0);
  readonly isEmptyState = computed(
    () => !this.loading() && this.store.filteredJobs().length === 0 && !this.error(),
  );

  /**
   * Empty-state flag for the unified work view (Phase 4).
   * P1: filter-aware — the projection is the truth, so an empty
   * PROJECTION (not an empty raw dataset) is the empty state.
   */
  readonly isEmptyWorkState = computed(() => {
    return this.viewMode() === 'all-work'
      && !this.workLoading()
      && this.store.filteredJobs().length === 0
      && !this.workError();
  });

  /**
   * Convenience boolean — true while the page is in the all-work view.
   */
  readonly isAllWorkView = computed(() => this.viewMode() === 'all-work');

  /**
   * Convenience accessors for the store's per-leg fetch flags so the
   * template keeps its pre-P1 binding names.
   */
  readonly jobsDegraded = this.store.jobsDegraded;
  readonly worksDegraded = this.store.worksDegraded;

  // Status filter options
  // M3 (mission-class, 2026-09-03) — ``settled`` added to the
  // dropdown so an operator can filter to mirror-receipt terminals
  // directly. Distinct from ``completed`` (task-side terminal) and
  // labelled "(receipt)" to surface the transport-vs-work split
  // without requiring the chip legend.
  readonly statusOptions: { value: JobStatus; label: string }[] = [
    { value: 'pending', label: 'Pending' },
    { value: 'processing', label: 'Processing' },
    { value: 'paused', label: 'Paused' },
    { value: 'completed', label: 'Completed' },
    { value: 'settled', label: 'Settled (receipt)' },
    { value: 'failed', label: 'Failed' },
    { value: 'cancelled', label: 'Cancelled' },
    { value: 'dead_letter', label: 'Dead Letter' }
  ];

  // Source filter options
  readonly sourceOptions: { value: JobSource | 'all'; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'api', label: 'API' },
    { value: 'telegram', label: 'Telegram' },
    { value: 'scheduler', label: 'Scheduler' },
    { value: 'webhook', label: 'Webhook' }
  ];

  // P1 — honest copy for the controls that are queues-view-only
  // (rendered in their place inside the All Work view; see the
  // template's @if branches). These state the BE gap instead of
  // shipping a no-op control (plan task 6: zero no-op controls).
  readonly sourceUnavailableCopy =
    'Source filter applies to the Queues view — work records carry no source';
  readonly showDeletedUnavailableCopy =
    'Deleted-job filter applies to the Queues view — work records have no deleted state';

  // Project filter options — derive from ProjectService.projects() and
  // lead with a sentinel empty-string option so the user can deselect
  // the project. Matches the shape SearchableSelectComponent expects
  // ({value, label}).
  readonly projectOptions = computed(() => [
    { value: '', label: 'Select project' },
    ...this.projects().map((p) => ({ value: p.project_id, label: p.name })),
  ]);

  // Agent filter options — derive from agents() and lead with an
  // "all" sentinel so the filter can clear. Label uses the existing
  // getAgentDisplayName helper so the dropdown stays consistent with
  // the legacy mat-option rendering (icon + name).
  readonly agentOptions = computed(() => [
    { value: 'all', label: 'All Agents' },
    ...this.agents().map((a) => ({ value: a.id, label: this.getAgentDisplayName(a.id) })),
  ]);

  constructor() {
    // Effect to handle job status updates from SSE — the patch
    // itself lives in the store (both datasets, order-preserving).
    effect(() => {
      const latestStatus = this.jobSseService.latestStatus();
      if (latestStatus && latestStatus.job_id) {
        this.store.updateJobFromSse(latestStatus);
      }
    });

    // Effect to handle SSE errors with user-friendly messages
    effect(() => {
      const latestError = this.jobSseService.latestError();
      const state = this.jobSseService.connectionState();
      const attempt = this.retryAttempt();

      if (latestError) {
        console.error('[Jobs] SSE error:', latestError);
        // Show user-friendly error message based on state
        let displayMessage = latestError;

        if (state === 'retrying') {
          displayMessage = `Connection lost. Reconnecting... (attempt ${attempt})`;
        } else if (state === 'failed') {
          displayMessage = latestError;
        }

        this.snackBar.open(displayMessage, 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });

        // Clear the error after showing it to prevent duplicate notifications
        this.jobSseService.clearError();
      }
    });

    // Effect to restore last selected project from localStorage
    effect(() => {
      const projectList = this.projects();
      if (projectList.length > 0) {
        this.tryRestoreProject();
      }
    });

    // Effect to restore the persisted view mode ('queues' vs.
    // 'all-work') once on first read. Uses a guard flag so it does
    // not race with subsequent user-driven view-mode changes.
    effect(() => {
      if (this.viewModeRestored) {
        return;
      }
      this.viewModeRestored = true;
      this.tryRestoreViewMode();
    });
  }

  ngOnInit(): void {
    // P1 — both fetch legs live in the store; kick both off so
    // switching to the All Work view later is instantaneous (the
    // work fetch is harmless if the user never toggles the view).
    this.store.fetchJobs();
    this.loadAgents();
    this.loadProjects();
    this.startAutoRefresh();
    this.store.fetchWorks();
    // Phase 4 — fetch the bad-state preflight so the red-glow + tooltip
    // appear on first paint when the system has stale rows.
    this.refreshBadStateCount();
    // Phase 2 — seed tabVisible from the document state at mount
    // (SSR-safe: ``doc`` is the injected DOCUMENT token, which is
    // the platform's document — never ``window`` directly so tests
    // can stub it). Wire the visibilitychange listener.
    this.tabVisible.set(this.doc.visibilityState === 'visible');
    this.doc.addEventListener('visibilitychange', this.onVisibilityChange);
  }

  private tryRestoreProject(): void {
    if (this.projectRestored) {
      return;
    }
    this.projectRestored = true;

    let savedProjectId: string | null = null;
    try {
      savedProjectId = localStorage.getItem(this.STORAGE_KEY);
    } catch {
      // silently ignore
    }
    if (!savedProjectId) {
      return;
    }

    // Check if saved project still exists in the project list
    const projectExists = this.projects().some(p => p.project_id === savedProjectId);
    if (projectExists) {
      // Directly set the filter without fetching — ngOnInit already
      // fetched (the restored scope applies from the next refresh on;
      // pre-existing restore/fetch ordering is unchanged by P1).
      this.store.setFilters({ project_id: savedProjectId });
    } else {
      // Clear stale entry
      try {
        localStorage.removeItem(this.STORAGE_KEY);
      } catch {
        // silently ignore
      }
    }
  }

  /**
   * Restore the persisted view mode ('queues' vs 'all-work') from
   * localStorage. Wrapped in try/catch for private-browsing safety
   * — matches the pattern used by ``tryRestoreProject``.
   */
  private tryRestoreViewMode(): void {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(this.VIEW_MODE_KEY);
    } catch {
      return;
    }
    if (saved === 'queues' || saved === 'all-work') {
      this.store.setFilters({ view_mode: saved });
      // If the user previously left the page in all-work view, make
      // sure the work leg has data even if the ngOnInit fetch raced
      // with the restore.
      if (saved === 'all-work' && this.store.works().length === 0) {
        this.store.fetchWorks();
      }
    }
  }

  ngOnDestroy(): void {
    this.stopAutoRefresh();
    if (this.refocusTimer !== null) {
      clearTimeout(this.refocusTimer);
      this.refocusTimer = null;
    }
    // Phase 2 — detach the visibilitychange listener (the cleanup
    // pin in jobs-page.bindings.pins.spec.ts). Without this the
    // listener survives the component and re-fires after destroy.
    this.doc.removeEventListener('visibilitychange', this.onVisibilityChange);
    this.jobSseService.disconnect();
    this.jobSseService.clearEvents();
    if (this.sseSubscription) {
      this.sseSubscription.unsubscribe();
    }
  }

  private loadProjects(): void {
    this.projectService.listProjects().subscribe({
      next: () => {},
      error: (err) => {
        console.error('Failed to load projects:', err);
      }
    });
  }

  // ── P1 deletion note ────────────────────────────────────────────────
  // The pre-P1 ``loadJobs()`` / ``loadWorks()`` component methods are
  // DELETED. Their fetch logic (filter→query projection, per-leg error
  // handling, retain-last-data discipline) lives in ``JobsPageStore``
  // (``fetchJobs`` / ``fetchWorks``), where it serves BOTH view modes
  // from one pipeline. The pre-P1 ``loadWorks`` snackbar-on-error is
  // replaced by the store's ``worksError`` signal + the template's
  // error block — errors no longer bypass the view state.

  /**
   * Phase 4 — fetch the system-wide bad-state count from
   * ``GET /api/jobs/cleanup/preflight``. The endpoint is intentionally
   * NOT gated by ``is_write_paused`` (per reviewer correction W1) so
   * the badge keeps surfacing stale rows during database migrations
   * when the cleanup itself is blocked.
   *
   * Phase 5 — the preflight now also returns ``zombie_instance_count``
   * (parallel to ``bad_state_count``) which the confirm dialog uses to
   * surface a separate instance-termination warning.
   *
   * Errors are intentionally swallowed — the red-glow + tooltip are
   * UX-only and a transient preflight failure should not surface as
   * a snackbar to the operator.
   */
  private refreshBadStateCount(): void {
    const preflight = firstValueFrom(
      this.http.get<CleanupPreflight>('/api/jobs/cleanup/preflight')
    );
    // The preflight intentionally exposes only the defer count. Read the
    // existing defer-blocked surface as well so the dialog can preserve the
    // holder-specific remediation without adding a daemon-only field.
    const deferBlocked = firstValueFrom(
      this.http.get<DeferBlockedStatus>('/api/queues/defer-blocked')
    ).catch(() => null);

    Promise.all([preflight, deferBlocked])
      .then(([result, deferStatus]) => {
        this.badStateCount.set(result.bad_state_count);
        this.zombieInstanceCount.set(
          result.zombie_instance_count ?? 0
        );
        // WS4 — the live-vs-reap split + the separate defer count
        // feed the confirm dialog ("will remain" listing + the
        // by-design "deferred messages are not cancelled here" note).
        this.liveInstanceCount.set(result.live_instance_count ?? 0);
        this.liveInstanceIds.set(result.live_instance_ids ?? []);
        this.deferBlockedCount.set(result.defer_blocked_count ?? 0);
        this.deferHolderKind.set(
          deferBlockAction(deferStatus)?.holder.kind ?? null
        );
      })
      .catch(() => {
        // Fail silently — badge is UX-only.
      });
  }

  /**
   * P1 — the pre-P1 ``loadWorks()`` is deleted (see the store note
   * above). The fetch — including the ``root_only: false`` P-A
   * contract and the status/project projection onto ``WorkFilters`` —
   * is ``JobsPageStore.fetchWorks`` via ``toWorkFilters``.
   */

  private loadAgents(): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
      }
    });
  }

  /**
   * Phase 2 — the 30s poll ticks through ``shouldTick`` BEFORE
   * making the HTTP call. The gate is a pure function over four
   * inputs (``tabVisible``, ``drawerOpen``, ``modalOpen``,
   * ``fetchInFlight``) so a paused tick is structurally a no-op —
   * the timer keeps firing, the gate decides. The store's in-flight
   * guard also prevents double-fire when the gate accidentally
   * passes during a long request (belt + braces).
   *
   * Cadence: ``POLL_INTERVAL_MS`` (30000) from jobs-poll.model.ts;
   * pin lives in jobs-poll.model.spec.ts.
   */
  private startAutoRefresh(): void {
    this.refreshInterval = setInterval(() => {
      if (
        shouldTick({
          tabVisible: this.tabVisible(),
          drawerOpen: this.drawerOpen(),
          modalOpen: this.modalOpen(),
          fetchInFlight: this.fetchInFlight(),
        })
      ) {
        this.store.refreshActive();
      }
    }, POLL_INTERVAL_MS);
  }

  /**
   * Phase 2 — ``document.visibilitychange`` listener. Pauses the poll
   * while the tab is hidden (the gate above) and fires an immediate
   * refresh on refocus, debounced by ``REFOCUS_DEBOUNCE_MS`` so rapid
   * tab-switching does not storm the BE.
   *
   * The listener is added in ``ngOnInit`` and torn down in
   * ``ngOnDestroy`` (the ``removeEventListener`` call is the
   * cleanup pin in jobs-page.bindings.pins.spec.ts).
   */
  private readonly onVisibilityChange = (): void => {
    const visible = this.doc.visibilityState === 'visible';
    this.tabVisible.set(visible);
    if (!visible) {
      return;
    }
    // Refocus: schedule an immediate refresh if the gate is open
    // and the debounce window has elapsed. The pending timer id is
    // stored so a rapid second focus cancels the previous and
    // extends the debounce (the storm-mitigation rule).
    if (this.refocusTimer !== null) {
      clearTimeout(this.refocusTimer);
    }
    this.refocusTimer = setTimeout(() => {
      this.refocusTimer = null;
      if (
        shouldTick({
          tabVisible: true,
          drawerOpen: this.drawerOpen(),
          modalOpen: this.modalOpen(),
          fetchInFlight: this.fetchInFlight(),
        })
      ) {
        this.store.refreshActive();
      }
    }, REFOCUS_DEBOUNCE_MS);
  };

  private stopAutoRefresh(): void {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
      this.refreshInterval = null;
    }
  }

  // ── P1 deletion note ────────────────────────────────────────────────
  // The pre-P1 ``updateJobFromSse`` method is DELETED from this file —
  // it moved VERBATIM into ``JobsPageStore.updateJobFromSse`` (both
  // datasets, order-preserving map, present-as-null contract, M3
  // terminal stamping). The constructor's SSE effect now delegates.

  protected onRefresh(): void {
    // P1 — refresh the ACTIVE view's leg through the store.
    this.store.refreshActive();
    // Phase 4 — refresh the preflight count alongside the main list so
    // the red-glow + tooltip reflect post-refresh reality.
    this.refreshBadStateCount();
  }

  /**
   * Phase 2 — manual reload wired to the honesty banner. The banner
   * surfaces when the row count is at or above the wire cap so the
   * operator can ask for a fresh fetch without scrolling to the
   * page-level Refresh button. Calls the same store path as
   * ``onRefresh`` (the store's in-flight guard prevents overlap).
   */
  protected onReloadBanner(): void {
    this.onRefresh();
  }

  /**
   * Phase 2 — render-guard truncation affordance: switch to the
   * Queues view (the bounded window surface backed by
   * ``/api/jobs``'s clamp-100 wire) so the operator sees every row
   * the BE will return for this filter scope.
   *
   * No-op when the user is already in the Queues view (the
   * guard would not have fired here, but defensive).
   */
  protected onSwitchToQueuesView(): void {
    if (this.viewMode() === 'queues') {
      return;
    }
    this.onViewModeChange('queues');
  }

  /**
   * Phase 2 — retry from the errored empty-state card. The
   * banner above the list already shows the "Last refresh failed"
   * copy during a degraded render; this fires when the projection
   * itself is empty AND the last fetch failed (the errored
   * empty-state branch).
   */
  protected onRetryEmpty(): void {
    this.onRefresh();
  }

  /**
   * Phase 2 — clear-filters from the filterEmpty empty-state card.
   * Reuses the same store path as ``onClearFilters`` so the view
   * mode is preserved.
   */
  protected onClearFiltersForEmpty(): void {
    this.onClearFilters();
  }

  /**
   * Phase 2 — expansion-state helpers for ``cdk-virtual-scroll``.
   * The set is keyed by ``job_id`` so a card's expansion survives
   * virtual recycle (the card re-mounts; the input binding picks up
   * the same membership).
   */
  protected isCardExpanded(item: WindowItem): boolean {
    if (item.kind !== 'row') {
      return false;
    }
    return this.expandedJobIds().has(item.job.job_id);
  }

  protected onToggleExpansion(jobId: string): void {
    this.expandedJobIds.update((set) => {
      const next = new Set(set);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  }

  /**
   * Phase 2 — ``cdkVirtualFor`` track-by. The plan calls for
   * track-by-``job_id`` so DOM-stable identity survives recycle.
   * The ``WindowItem.key`` already encodes the right identity
   * (``job.job_id`` for rows; Phase 3 will set ``missionId`` for
   * headers), so the helper is identity on the key.
   */
  protected trackByKey = (_index: number, item: WindowItem): string => item.key;

  /**
   * Phase 2 — exposed reload accessible label. The plan calls for
   * an explicit accessible label on the banner Reload button (never
   * icon-only). Pinned via ``WINDOW_BANNER_COPY.reloadAccessibleLabel``.
   */
  protected readonly reloadAccessibleLabel = WINDOW_BANNER_COPY.reloadAccessibleLabel;

  /**
   * Phase 4 — switch between 'queues' (legacy) and 'all-work'
   * (unified list backed by /api/work). Persists the choice so it
   * survives a reload.
   *
   * Switching INTO 'all-work' triggers an immediate fetch if the
   * work list is empty — the initial ngOnInit fetch may have raced
   * with the first paint and we do not want the user to see a
   * stale blank list.
   */
  protected onViewModeChange(mode: JobsViewMode): void {
    if (this.viewMode() === mode) {
      return;
    }
    // P1 — view_mode is a JobsFilterState key; the projection flips
    // datasets inside the ONE pipeline (no second fetch path).
    this.store.setFilters({ view_mode: mode });
    try {
      localStorage.setItem(this.VIEW_MODE_KEY, mode);
    } catch {
      // Private-browsing — silently ignore.
    }
    if (mode === 'all-work' && this.works().length === 0) {
      this.store.fetchWorks();
    }
  }

  /**
   * Status filter — server-side on BOTH wires (jobs + work), so both
   * legs refetch; the client-side projection re-applies the same key.
   */
  protected onStatusFilterChange(statuses: JobStatus[]): void {
    this.store.setFilters({ status: statuses.length > 0 ? statuses : [] });
    this.store.fetchJobs();
    this.store.fetchWorks();
  }

  /**
   * Source filter — WINDOW-SCOPED, client-side only (BE gap-e5: the
   * jobs list endpoint ignores ``source``; the work endpoint has no
   * source concept at all). A source change must NOT refetch — the
   * projection re-derives instantly from the fetched window. The
   * control is queues-view-only (hidden in all-work with honest
   * copy, since work rows carry no source to match).
   */
  protected onSourceFilterChange(source: JobSource | 'all'): void {
    this.store.setFilters({ source: source === 'all' ? null : source });
  }

  /**
   * Agent filter — WINDOW-SCOPED, client-side in BOTH views (``Work``
   * rows carry ``agent_id``, so the projection can match them). No
   * refetch — same instant re-projection as source.
   */
  protected onAgentFilterChange(agentId: string): void {
    this.store.setFilters({ agent_id: agentId === 'all' ? null : agentId });
  }

  /**
   * Project filter — server-side on BOTH wires; clears the queue
   * selection (a queue belongs to a project) and persists the choice.
   */
  protected onProjectFilterChange(projectId: string): void {
    this.store.setFilters({ project_id: projectId || null, queue_id: null });
    // Persist selection to localStorage
    try {
      if (projectId) {
        localStorage.setItem(this.STORAGE_KEY, projectId);
      } else {
        localStorage.removeItem(this.STORAGE_KEY);
      }
    } catch {
      // silently ignore
    }
    this.store.fetchJobs();
    this.store.fetchWorks();
  }

  protected onClearFilters(): void {
    // P1 — store.clearFilters preserves the active view mode.
    this.store.clearFilters();
    // Clear localStorage so the project isn't silently restored on next visit
    try {
      localStorage.removeItem(this.STORAGE_KEY);
    } catch {
      // silently ignore
    }
    this.store.fetchJobs();
    this.store.fetchWorks();
  }

  /**
   * Show-deleted toggle — server-side on the jobs wire only
   * (``/api/work`` has no soft-delete concept). Queues-view-only
   * control (hidden in all-work with honest copy).
   */
  protected onToggleShowDeleted(checked: boolean): void {
    this.store.setFilters({ include_deleted: checked });
    this.store.fetchJobs();
  }

  /**
   * Queue selection — queues-view-only (server-side on the jobs
   * wire; work rows carry no queue_id).
   */
  protected onQueueSelected(queueId: string | null): void {
    this.store.setFilters({ queue_id: queueId || null });
    this.store.fetchJobs();
  }

  protected onQueueChanged(): void {
    this.store.fetchJobs();
  }

  protected onOpenCreateDialog(): void {
    // Phase 2 — flip the poll gate's ``modalOpen`` so the 30s tick
    // pauses while the create dialog is up. The previous snackbar
    // spam during long dialog sessions came from the poll replacing
    // the list mid-dialog; the gate + retain-last-data discipline
    // kill both surfaces.
    this.modalOpen.set(true);
    const dialogRef = this.dialog.open(JobCreateDialogComponent, {
      width: '500px',
      panelClass: 'dark-modal-panel',
      data: {
        agentId: 'leader',
        projectId: this.selectedProjectId() || undefined
      }
    });

    dialogRef.afterClosed().subscribe((result: JobCreateDialogResult | undefined) => {
      this.modalOpen.set(false);
      if (result) {
        this.createJob(result);
      }
    });
  }

  private createJob(data: JobCreateDialogResult): void {
    this.jobService.createJob({
      agent_id: data.agent_id,
      message: data.message,
      project_id: data.project_id,
      priority: data.priority,
      source: data.source as JobSource,
      queue_id: data.queue_id
    }).subscribe({
      next: (job) => {
        this.snackBar.open('Job created successfully', 'Close', {
          duration: 3000,
          panelClass: 'success-snackbar'
        });
        this.store.fetchJobs();
      },
      error: (err) => {
        console.error('Failed to create job:', err);
        this.snackBar.open(
          err.message || 'Failed to create job',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  /**
   * Cancel a job — single entry point that covers BOTH the
   * ``JobCardComponent`` (cancel button on the card) and the
   * ``JobDetailDrawerComponent`` (cancel button in the drawer). The
   * ``onDrawerCancelJob`` helper also routes through here so the
   * confirmation dialog is applied uniformly to every cancel entry.
   *
   * Behavior:
   *   * Open the reusable ``ConfirmDialogComponent`` with the
   *     destructive-action copy ("Cancel Job" / "Yes, Cancel Job").
   *   * If the dialog resolves to ``true``, fire the actual
   *     ``jobService.cancelJob`` request and show a success snackbar.
   *   * If the dialog resolves to ``false`` or ``undefined`` (dismiss
   *     or Esc), do NOTHING — no service call, no snackbar. The job
   *     stays in its current state.
   *   * Errors from the backend surface as an error snackbar — the
   *     dialog is already closed by this point so the user can read
   *     the snackbar.
   */
  protected onCancelJob(job: Job): void {
    // Phase 2 — flip the poll gate's ``modalOpen`` (the cancel
    // confirm IS a modal from the gate's perspective).
    this.modalOpen.set(true);
    const dialogRef = this.dialog.open<ConfirmDialogComponent, ConfirmDialogData, boolean>(
      ConfirmDialogComponent,
      {
        width: '420px',
        panelClass: 'dark-modal-panel',
        data: {
          title: 'Cancel Job',
          message: 'Are you sure you want to cancel this job? This action cannot be undone.',
          confirmLabel: 'Yes, Cancel Job',
          cancelLabel: 'Cancel',
          destructive: true,
        },
      },
    );

    dialogRef.afterClosed().subscribe((confirmed: boolean | undefined) => {
      this.modalOpen.set(false);
      if (!confirmed) {
        return;
      }
      this.jobService.cancelJob(job.job_id).subscribe({
        next: () => {
          this.snackBar.open('Job cancelled', 'Close', {
            duration: 3000
          });
        },
        error: (err) => {
          console.error('Failed to cancel job:', err);
          this.snackBar.open(
            err.message || 'Failed to cancel job',
            'Dismiss',
            {
              duration: 5000,
              panelClass: 'error-snackbar'
            }
          );
        }
      });
    });
  }

  protected onRetryJob(job: Job): void {
    this.jobService.retryJob(job.job_id).subscribe({
      next: () => {
        this.snackBar.open('Job retry scheduled', 'Close', {
          duration: 3000
        });
      },
      error: (err) => {
        console.error('Failed to retry job:', err);
        this.snackBar.open(
          err.message || 'Failed to retry job',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  protected onDeleteJob(job: Job): void {
    this.jobService.softDeleteJob(job.job_id).subscribe({
      next: () => {
        this.snackBar.open('Job deleted', 'Undo', { duration: 5000 })
          .onAction().subscribe(() => {
            this.jobService.restoreJob(job.job_id).subscribe({
              next: () => this.store.fetchJobs(),
              error: () => {}
            });
          });
        if (!this.showDeleted()) {
          // Remove from local list (store-owned mutation seam)
          this.store.removeJob(job.job_id);
        } else {
          // Update the job in place (show as deleted)
          this.store.patchJob(job.job_id, { deleted_at: new Date().toISOString() });
        }
      },
      error: (err) => {
        this.snackBar.open(err.message || 'Failed to delete job', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onRestoreJob(job: Job): void {
    this.jobService.restoreJob(job.job_id).subscribe({
      next: () => this.store.fetchJobs(),
      error: (err) => {
        this.snackBar.open(err.message || 'Failed to restore job', 'Dismiss', {
          duration: 5000,
          panelClass: 'error-snackbar'
        });
      }
    });
  }

  protected onRetryAllDeadLetterJobs(): void {
    const projectId = this.filters().project_id;
    if (!projectId) {
      this.snackBar.open('Please select a project first', 'Dismiss', {
        duration: 3000,
        panelClass: 'error-snackbar'
      });
      return;
    }

    this.retryingAll.set(true);
    this.jobService.retryAllDeadLetterJobs(projectId).subscribe({
      next: (result) => {
        this.retryingAll.set(false);
        this.snackBar.open(
          `Replayed ${result.replayed} job${result.replayed !== 1 ? 's' : ''}${result.failed > 0 ? `, ${result.failed} failed` : ''}`,
          'Close',
          { duration: 5000 }
        );
        this.store.fetchJobs();
      },
      error: (err) => {
        console.error('Failed to retry all dead letter jobs:', err);
        this.retryingAll.set(false);
        this.snackBar.open(
          err.message || 'Failed to retry all dead letter jobs',
          'Dismiss',
          {
            duration: 5000,
            panelClass: 'error-snackbar'
          }
        );
      }
    });
  }

  /**
   * Open the System Cleanup confirmation dialog and (on confirm) call
   * ``POST /api/jobs/cleanup`` to cancel every queued and active job
   * across all projects.
   *
   * Guards:
   *   * Cleanup already in progress — silently no-op so a double-click
   *     cannot fire two parallel backend requests.
   *
   * The backend endpoint is intentionally global (it cancels across
   * all projects — this is a "system reset" operation), so no project
   * filter is required here. The confirmation dialog makes the global
   * scope explicit.
   *
   * The backend now also reaps *orphan* active jobs — rows whose
   * underlying instance is already terminal but whose
   * ``admission_state='active'`` was leaked (e.g. observer feedback
   * dropped because the worker process died mid-ack). They surface
   * as ``orphaned_reaped`` in the response and are surfaced in the
   * success snackbar so the operator can see the ghost rows were
   * drained without a second round-trip.
   *
   * On success a success snackbar reports the cancelled counts and the
   * active view is refreshed; on error an error snackbar surfaces the
   * failure message. The ``cleanupInProgress`` signal is always reset
   * before the method returns so the button re-enables.
   */
  protected onSystemCleanup(): void {
    if (this.cleanupInProgress()) {
      return;
    }

    // Phase 2 — flip the poll gate's ``modalOpen`` (the confirm
    // dialog IS a modal from the gate's perspective).
    this.modalOpen.set(true);
    const dialogRef = this.dialog.open(SystemCleanupConfirmDialogComponent, {
      width: '420px',
      panelClass: 'dark-modal-panel',
      // Phase 4 — surface the bad-state preflight count in the
      // confirmation dialog so the operator sees a warning before
      // committing to a full system cleanup. Phase 5 — also surface
      // the zombie-instance count so the operator sees how many
      // non-terminal instances (with no live work) are about to be
      // terminated. The dialog component is responsible for the
      // conditional rendering.
      data: {
        bad_state_count: this.badStateCount(),
        zombie_instance_count: this.zombieInstanceCount(),
        // WS4 — the full live-vs-reap split + separate defer count.
        live_instance_count: this.liveInstanceCount(),
        live_instance_ids: this.liveInstanceIds(),
        defer_blocked_count: this.deferBlockedCount(),
        defer_holder_kind: this.deferHolderKind(),
      },
    });

    dialogRef.afterClosed().subscribe((confirmed: boolean | undefined) => {
      this.modalOpen.set(false);
      if (!confirmed) {
        return;
      }
      this.cleanupInProgress.set(true);
      this.jobService.cleanupAllJobs().subscribe({
        next: (result) => {
          this.cleanupInProgress.set(false);
          const orphaned = result.orphaned_reaped ?? 0;
          const reconciled = result.reconciled_bad_state ?? 0;
          const terminated = result.terminated_instances ?? 0;
          const parts: string[] = [
            `Cancelled ${result.cancelled_queued} queued`,
            `${result.cancelled_active} active`,
          ];
          if (orphaned > 0) parts.push(`${orphaned} orphaned`);
          if (reconciled > 0) parts.push(`${reconciled} bad-state`);
          if (terminated > 0) parts.push(`${terminated} instances terminated`);
          this.snackBar.open(
            `${parts.join(', ')} jobs`,
            'Close',
            { duration: 3000, panelClass: 'success-snackbar' }
          );
          this.onRefresh();
          // Phase 4 — refresh the preflight after a successful cleanup
          // so the red-glow + tooltip clear once the rows are gone.
          this.refreshBadStateCount();
        },
        error: (err) => {
          console.error('Failed to cleanup jobs:', err);
          this.cleanupInProgress.set(false);
          this.snackBar.open(
            err?.message || 'Failed to cleanup jobs',
            'Dismiss',
            {
              duration: 5000,
              panelClass: 'error-snackbar'
            }
          );
        },
      });
    });
  }

  protected onViewJobDetails(job: Job): void {
    this.selectedJob.set(job);
    this.drawerOpen.set(true);

    // Don't connect to SSE for terminal jobs - no live updates needed
    if (isTerminalStatus(job.status)) {
      return;
    }

    // Connect to SSE for real-time updates on this job
    this.jobSseService.disconnect();
    this.jobSseService.clearEvents();
    this.sseSubscription = this.jobSseService.streamJobEvents(job.job_id).subscribe();
  }

  protected onCloseDrawer(): void {
    this.drawerOpen.set(false);
    this.selectedJob.set(null);
    this.jobSseService.disconnect();
    if (this.sseSubscription) {
      this.sseSubscription.unsubscribe();
      this.sseSubscription = null;
    }
  }

  protected onDrawerCancelJob(jobId: string): void {
    const job = this.store.findJob(jobId);
    if (job) {
      this.onCancelJob(job);
    }
  }

  protected onDrawerRetryJob(jobId: string): void {
    const job = this.store.findJob(jobId);
    if (job) {
      this.onRetryJob(job);
    }
  }

  protected onDrawerViewInstance(instanceId: string): void {
    const projectContext = this.tabStateService.activeProjectId() ?? 'all';
    this.router.navigate(['/projects', projectContext, 'instances', instanceId]);
  }

  protected getAgentDisplayName(agentId: string): string {
    const agent = this.agents().find(a => a.id === agentId);
    return agent ? `${agent.icon} ${agent.name}` : agentId;
  }

  protected hasActiveFilters(): boolean {
    // P1 — the single model helper (status/source/agent_id/queue_id;
    // project_id is a scope selection, not a clearable filter).
    return hasActiveJobsFilter(this.store.filterState());
  }

  protected isProjectSelected(): boolean {
    return !!this.filters().project_id;
  }

  // Handle project pause change from queue-list header
  protected onProjectPauseChanged(isPaused: boolean): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;

    // Update local project state
    this.projectService.projects.update(projects => 
      projects.map(p => 
        p.project_id === projectId 
          ? { ...p, job_queue_paused: isPaused }
          : p
      )
    );
  }
}
