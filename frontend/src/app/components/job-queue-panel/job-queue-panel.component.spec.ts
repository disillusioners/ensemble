import { signal, computed } from '@angular/core';
import {
  getStatusColor as modelGetStatusColor,
  Job,
  JobStatus,
  MissionSummary,
  MissionLiveness,
  missionLivenessChip,
  buildQueueTree,
  shouldAutoExpand,
  missionDisplayTitle,
  visibleTreeItems,
  nextVisibleItem,
  visibleTreeItemId,
  VisibleTreeItem,
} from '../../models/job.model';
import { createMockJob, createMockLiveMissionReceipt } from '../../testing/job-test-helpers';

/**
 * Logic-mirror of JobQueuePanelComponent.
 *
 * This project does NOT use Angular TestBed for component tests —
 * see ``job-queue-indicator.component.spec.ts`` and
 * ``job-detail-drawer.component.spec.ts`` for the same pattern. We
 * replicate the component's signal/computed logic and its helper
 * methods (resolveTitle, projectLabel, shortenId, timeAgo, status
 * helpers) in a plain TS class so the assertions below can exercise
 * priority chains, capping, formatting, and empty-state detection
 * without Angular DI.
 *
 * The class exposes the same input signals as the real component but
 * also small `setX` helper methods so the tests can drive the inputs
 * explicitly. The behaviour of every helper mirrors the real component
 * bit-for-bit; if the real component changes, this mirror must change
 * in lockstep.
 */
class MockJobQueuePanelComponent {
  private readonly _activeJobs = signal<Job[]>([]);
  private readonly _recentJobs = signal<Job[]>([]);
  private readonly _projectNameMap = signal<Map<string | null, string>>(new Map());
  private readonly _liveMissionCount = signal<number | null>(null);
  private readonly _missions = signal<MissionSummary[]>([]);

  readonly MAX_RECENT = 10;

  activeJobs = this._activeJobs.asReadonly();
  recentJobs = this._recentJobs.asReadonly();
  projectNameMap = this._projectNameMap.asReadonly();
  liveMissionCount = this._liveMissionCount.asReadonly();
  missions = this._missions.asReadonly();

  /** Mock output — mirrors the real component's `output<Job>()`. */
  readonly jobClick = { emit: jest.fn() };

  /**
   * T1 mock output — mirrors the real component's ``output<void>()``
   * for the panel footer's "Open full queue →" activation. The
   * indicator handles it (navigate + close menu); the panel stays
   * DUMB/presentational, exactly like ``jobClick`` does for rows.
   */
  readonly footerClick = { emit: jest.fn() };

  /**
   * T3 mock — ``visibleTreeItems`` mirror: the flattened list of
   * items the arrow-key handler can land on. Built from the panel's
   * tree + expansion sets so the spec can pin the helper output
   * without an Angular harness.
   */
  readonly visibleItems = computed(() =>
    visibleTreeItems(this.tree(), this._expandedLiveMissions(), this._expandedRecentMissions())
  );

  /**
   * T3 mock — ``focusedItemId`` signal mirror (the row that owns
   * keyboard focus inside the trees). Drives the ``.focused`` class
   * in the template; tests drive it directly to exercise the
   * traversal logic.
   */
  private readonly _focusedItemId = signal<string | null>(null);
  readonly focusedItemId = this._focusedItemId.asReadonly();

  /** Tree derivation — mirrors the real component's ``tree`` computed. */
  readonly tree = computed(() =>
    buildQueueTree(this._activeJobs(), this._recentJobs(), this._missions())
  );

  /** Auto-expand decision — mirrors the real component. */
  readonly shouldAutoExpandLive = computed(() =>
    shouldAutoExpand(this.tree().liveMissions)
  );

  recentCapped = computed(() => this._recentJobs().slice(0, this.MAX_RECENT));
  isEmpty = computed(
    () =>
      this._activeJobs().length === 0 &&
      this.recentCapped().length === 0 &&
      (this._liveMissionCount() ?? 0) === 0,
  );
  activeCount = computed(() => this._activeJobs().length);

  /**
   * Fix C (§8.2) mirror — mission-liveness chip for a row, or null
   * when the row renders nothing extra. Calls the SAME model helper
   * the real component calls.
   */
  missionChip(job: Job) {
    return missionLivenessChip(job);
  }

  setLiveMissionCount(n: number | null): void {
    this._liveMissionCount.set(n);
  }

  setMissions(m: MissionSummary[]): void {
    this._missions.set(m);
  }

  /**
   * Expansion-state survival mirror — keys the expansion set on
   * ``mission_id`` (NOT array index) so a poll refresh that
   * re-orders the live missions does NOT collapse the user's
   * expanded state. Mirrors the real component's
   * ``expandedLiveMissions`` signal + ``toggleLiveMission`` /
   * ``isLiveExpanded`` methods.
   */
  private readonly _expandedLiveMissions = signal<Set<string>>(new Set());

  toggleLiveMission(missionId: string): void {
    this._expandedLiveMissions.update((s) => {
      const next = new Set(s);
      if (next.has(missionId)) next.delete(missionId);
      else next.add(missionId);
      return next;
    });
  }

  isLiveExpanded(missionId: string | null | undefined): boolean {
    if (!missionId) return false;
    return this._expandedLiveMissions().has(missionId);
  }

  /** Read-only view of the expansion set — used by the survival test
   *  to assert the user's expanded state survived a poll refresh. */
  readonly expandedLiveMissions = this._expandedLiveMissions.asReadonly();

  /**
   * T3 mock — per-mission-id expansion state for RECENT mission
   * nodes. Same survival guarantee as the live expansion set.
   */
  private readonly _expandedRecentMissions = signal<Set<string>>(new Set());
  toggleRecentMission(missionId: string): void {
    this._expandedRecentMissions.update((s) => {
      const next = new Set(s);
      if (next.has(missionId)) next.delete(missionId);
      else next.add(missionId);
      return next;
    });
  }
  isRecentExpanded(missionId: string | null | undefined): boolean {
    if (!missionId) return false;
    return this._expandedRecentMissions().has(missionId);
  }

  /**
   * T3 mock — true iff the given ``visibleTreeItemId`` is the row
   * that currently owns keyboard focus. Mirrors the real
   * component's ``isFocusedItem`` read-side.
   */
  isFocusedItem(id: string | null | undefined): boolean {
    if (!id) return false;
    return this._focusedItemId() === id;
  }

  /** T3 mock — drives the focused item (mirrors ``onRowFocus``). */
  onRowFocus(id: string): void {
    this._focusedItemId.set(id);
  }

  /**
   * T3 mock — arrow-key handler. Mirrors the real component's
   * ``onTreeKeydown`` 1:1 so the key→action map and the
   * nextVisibleItem traversal can be pinned without DOM. Other
   * keys are no-ops; Enter/Space/Esc are NOT handled here (those
   * stay on the individual rows).
   */
  onTreeKeydown(event: KeyboardEvent): void {
    const items = this.visibleItems();
    if (items.length === 0) return;
    const key = event.key;
    if (
      key !== 'ArrowDown' &&
      key !== 'ArrowUp' &&
      key !== 'ArrowRight' &&
      key !== 'ArrowLeft'
    ) {
      return;
    }
    // Prevent the page from scrolling on ArrowDown/Up inside the
    // menu and stop the event from bubbling — mirrors the real
    // component bit-for-bit.
    event.preventDefault();
    event.stopPropagation();
    const currentId = this._focusedItemId();
    const currentIndex = currentId
      ? items.findIndex((it) => visibleTreeItemId(it) === currentId)
      : -1;

    if (key === 'ArrowDown' || key === 'ArrowUp') {
      const delta: -1 | 1 = key === 'ArrowUp' ? -1 : 1;
      const nextIndex = nextVisibleItem(items, currentIndex, delta);
      this._focusedItemId.set(visibleTreeItemId(items[nextIndex]));
      return;
    }

    if (currentIndex < 0) return;
    const current = items[currentIndex];
    if (key === 'ArrowRight') {
      if (current.kind !== 'mission') return;
      const id = current.node.mission.mission_id;
      if (!id) return;
      if (current.tree === 'live') {
        if (!this.isLiveExpanded(id)) this.toggleLiveMission(id);
      } else {
        if (!this.isRecentExpanded(id)) this.toggleRecentMission(id);
      }
      return;
    }
    // ArrowLeft
    let missionId: string | null | undefined;
    let tree: 'live' | 'recent';
    if (current.kind === 'mission') {
      missionId = current.node.mission.mission_id;
      tree = current.tree;
    } else {
      missionId = current.mission.mission_id;
      tree = current.tree;
    }
    if (!missionId) return;
    const isExpanded =
      tree === 'live'
        ? this.isLiveExpanded(missionId)
        : this.isRecentExpanded(missionId);
    if (!isExpanded) return;
    if (tree === 'live') this.toggleLiveMission(missionId);
    else this.toggleRecentMission(missionId);
  }

  /** T1 mock — footer activation emits ``footerClick``. */
  onFooterClick(): void {
    this.footerClick.emit();
  }

  /**
   * Resolves the best available title for a job. Priority chain:
   * 1. job_metadata.instance_name (if truthy)
   * 2. agent_id (if truthy)
   * 3. shortenId of instance_id (or job_id) as a last resort
   */
  resolveTitle(job: Job): string {
    const meta = job.job_metadata;
    if (meta && typeof meta === 'object' && meta['instance_name']) {
      return String(meta['instance_name']);
    }

    if (job.agent_id) {
      return job.agent_id;
    }

    return this.shortenId(job.instance_id ?? job.job_id);
  }

  projectLabel(job: Job): string {
    const id = job.project_id;
    if (id === null || id === undefined) return '—';
    return this._projectNameMap().get(id) ?? this.shortenId(id);
  }

  shortenId(id: string | null | undefined): string {
    if (!id) return '—';
    return id.length > 8 ? id.substring(0, 8) + '...' : id;
  }

  timeAgo(dateString: string | null | undefined): string {
    if (!dateString) return '';
    const now = new Date();
    const date = new Date(dateString);
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString();
  }

  /**
   * Mission title — mirrors the real component's ``missionTitle`` helper.
   */
  missionTitle(m: MissionSummary): string {
    return missionDisplayTitle(m, (d) => this.timeAgo(d));
  }

  getStatusIcon(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'failed':
        return 'error';
      case 'cancelled':
        return 'cancel';
      case 'dead_letter':
        return 'inventory_2';
      default:
        return 'info';
    }
  }

  /** Delegate to the shared util — same identity as the real component. */
  readonly getStatusColor = modelGetStatusColor;

  /** Emits the clicked job up to the parent for navigation. */
  onRowClick(job: Job): void {
    this.jobClick.emit(job);
  }

  /** Test helpers — mirror the writable inputs of the real component. */
  setActiveJobs(jobs: Job[]): void {
    this._activeJobs.set(jobs);
  }

  setRecentJobs(jobs: Job[]): void {
    this._recentJobs.set(jobs);
  }

  setProjectNameMap(map: Map<string | null, string>): void {
    this._projectNameMap.set(map);
  }
}

describe('JobQueuePanelComponent Logic', () => {
  let component: MockJobQueuePanelComponent;

  beforeEach(() => {
    component = new MockJobQueuePanelComponent();
  });

  describe('instantiation', () => {
    it('should create the logic-mirror component', () => {
      expect(component).toBeTruthy();
    });

    it('should default to empty state and 0 running', () => {
      expect(component.isEmpty()).toBe(true);
      expect(component.activeCount()).toBe(0);
      expect(component.recentCapped().length).toBe(0);
    });
  });

  describe('resolveTitle priority chain', () => {
    it('should prefer job_metadata.instance_name over agent_id', () => {
      const job = createMockJob({
        instance_id: 'inst-1',
        agent_id: 'developer',
        job_metadata: { instance_name: 'Metadata Name' },
      });
      expect(component.resolveTitle(job)).toBe('Metadata Name');
    });

    it('should fall back to agent_id when no metadata', () => {
      const job = createMockJob({
        instance_id: 'inst-1',
        agent_id: 'developer',
        job_metadata: null,
      });
      expect(component.resolveTitle(job)).toBe('developer');
    });

    it('should fall back to shortened id when nothing else is available', () => {
      // Build the job directly so we can omit agent_id entirely
      // (test helper's `Job` requires a non-empty string).
      const job: Job = {
        job_id: 'abcdef1234567890',
        agent_id: 'placeholder',
        project_id: null,
        priority: 5,
        status: 'processing',
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        instance_id: 'instance-abc-12345',
        error_message: null,
        result_summary: null,
        job_metadata: null,
        cancelled_at: null,
      };
      // Force the fallback by clearing the agent_id field via a
      // second, falsy-overridden instance.
      const fallen: Job = { ...job, agent_id: '' as unknown as string };
      expect(component.resolveTitle(fallen)).toBe('instance...');
    });

    it('should skip empty instance_name in metadata', () => {
      const job = createMockJob({
        instance_id: 'inst-1',
        agent_id: 'developer',
        job_metadata: { instance_name: '' },
      });
      expect(component.resolveTitle(job)).toBe('developer');
    });

    it('should fall back to job_id when instance_id is null and agent_id is empty', () => {
      const job = createMockJob({
        job_id: 'job-abc-1234',
        instance_id: null,
      });
      const fallen: Job = { ...job, agent_id: '' as unknown as string };
      expect(component.resolveTitle(fallen)).toBe('job-abc-...');
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

    it('should return em-dash for null', () => {
      expect(component.shortenId(null)).toBe('—');
    });

    it('should return em-dash for undefined', () => {
      expect(component.shortenId(undefined)).toBe('—');
    });

    it('should return em-dash for empty string', () => {
      expect(component.shortenId('')).toBe('—');
    });
  });

  describe('projectLabel', () => {
    it('should use the cached project name when present', () => {
      component.setProjectNameMap(new Map([['proj-1', 'Alpha Project']]));
      const job = createMockJob({ project_id: 'proj-1' });
      expect(component.projectLabel(job)).toBe('Alpha Project');
    });

    it('should fall back to shortened id when name is missing', () => {
      component.setProjectNameMap(new Map());
      const job = createMockJob({ project_id: 'project-1234-abc' });
      expect(component.projectLabel(job)).toBe('project-...');
    });

    it('should return em-dash for null project_id', () => {
      component.setProjectNameMap(new Map([['proj-1', 'Alpha']]));
      const job = createMockJob({ project_id: null });
      expect(component.projectLabel(job)).toBe('—');
    });
  });

  describe('timeAgo', () => {
    it('should return "just now" for timestamps within the last minute', () => {
      const now = new Date().toISOString();
      expect(component.timeAgo(now)).toBe('just now');
    });

    it('should return "Xm ago" for minutes', () => {
      const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
      expect(component.timeAgo(fiveMinAgo)).toBe('5m ago');
    });

    it('should return "Xh ago" for hours', () => {
      const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
      expect(component.timeAgo(twoHoursAgo)).toBe('2h ago');
    });

    it('should return "Xd ago" for days', () => {
      const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
      expect(component.timeAgo(threeDaysAgo)).toBe('3d ago');
    });

    it('should return empty string for null', () => {
      expect(component.timeAgo(null)).toBe('');
    });

    it('should return empty string for undefined', () => {
      expect(component.timeAgo(undefined)).toBe('');
    });

    it('should return a locale date for items older than 7 days', () => {
      const oldDate = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
      const result = component.timeAgo(oldDate);
      // We don't pin the exact locale string; just verify it's NOT a
      // "Xd ago" / "Xh ago" form and is non-empty.
      expect(result).toBeTruthy();
      expect(result).not.toMatch(/ago$/);
    });
  });

  describe('isEmpty', () => {
    it('should be true when both running and recent are empty', () => {
      component.setActiveJobs([]);
      component.setRecentJobs([]);
      expect(component.isEmpty()).toBe(true);
    });

    it('should be false when activeJobs has items', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when recentJobs has items', () => {
      component.setActiveJobs([]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when both lists have items', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should react to signal updates', () => {
      expect(component.isEmpty()).toBe(true);
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      expect(component.isEmpty()).toBe(false);
      component.setActiveJobs([]);
      expect(component.isEmpty()).toBe(true);
    });
  });

  describe('recentCapped', () => {
    it('should cap recent jobs at 10', () => {
      const jobs = Array.from({ length: 15 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(10);
    });

    it('should preserve order when capping', () => {
      const jobs = Array.from({ length: 15 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped()[0].job_id).toBe('job-0');
      expect(component.recentCapped()[9].job_id).toBe('job-9');
    });

    it('should not cap when fewer than 10 jobs', () => {
      const jobs = Array.from({ length: 5 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(5);
    });

    it('should pass through an empty list unchanged', () => {
      component.setRecentJobs([]);
      expect(component.recentCapped()).toEqual([]);
    });

    it('should cap exactly 10 jobs to 10 (boundary)', () => {
      const jobs = Array.from({ length: 10 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(10);
    });

    it('should cap 11 jobs to 10 (boundary)', () => {
      const jobs = Array.from({ length: 11 }, (_, i) =>
        createMockJob({ job_id: `job-${i}` }),
      );
      component.setRecentJobs(jobs);
      expect(component.recentCapped().length).toBe(10);
    });
  });

  describe('activeCount', () => {
    it('should reflect the number of active jobs (running + pending + paused)', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
        createMockJob({ status: 'pending' }),
      ]);
      expect(component.activeCount()).toBe(3);
    });

    it('should not count recent jobs', () => {
      component.setActiveJobs([]);
      component.setRecentJobs([
        createMockJob({ status: 'completed' }),
        createMockJob({ status: 'failed' }),
      ]);
      expect(component.activeCount()).toBe(0);
    });
  });

  describe('status helpers', () => {
    it('should map completed to check_circle and green', () => {
      expect(component.getStatusIcon('completed')).toBe('check_circle');
      expect(component.getStatusColor('completed')).toBe('#22C55E');
    });

    it('should map failed to error and red', () => {
      expect(component.getStatusIcon('failed')).toBe('error');
      expect(component.getStatusColor('failed')).toBe('#EF4444');
    });

    it('should map cancelled to cancel and amber', () => {
      expect(component.getStatusIcon('cancelled')).toBe('cancel');
      expect(component.getStatusColor('cancelled')).toBe('#F59E0B');
    });

    it('should map dead_letter to inventory_2 and purple', () => {
      expect(component.getStatusIcon('dead_letter')).toBe('inventory_2');
      expect(component.getStatusColor('dead_letter')).toBe('#7C3AED');
    });

    it('should fall back to info/grey for non-terminal statuses', () => {
      // pending, processing, paused all fall through the switch to
      // the default branch — that's the "unknown" fallback path.
      expect(component.getStatusIcon('pending')).toBe('info');
      expect(component.getStatusColor('pending')).toBe('#9CA3AF');
      expect(component.getStatusIcon('processing')).toBe('info');
      expect(component.getStatusColor('processing')).toBe('#3B82F6');
      expect(component.getStatusIcon('paused')).toBe('info');
      expect(component.getStatusColor('paused')).toBe('#F59E0B');
    });

    it('should delegate getStatusColor to the shared model util', () => {
      // Reference identity locks in the delegation; the component
      // must NOT define its own color table.
      expect(component.getStatusColor).toBe(modelGetStatusColor);
    });
  });

  describe('integration — priority chain with mixed data', () => {
    it('should resolve different titles for a mixed list', () => {
      component.setActiveJobs([
        createMockJob({
          job_id: 'job-1',
          instance_id: 'inst-A',
          agent_id: 'developer',
          job_metadata: { instance_name: 'From Metadata A' },
          status: 'processing',
        }),
        createMockJob({
          job_id: 'job-2',
          instance_id: 'inst-B',
          agent_id: 'developer',
          job_metadata: null,
          status: 'processing',
        }),
        createMockJob({
          job_id: 'job-3',
          instance_id: null,
          agent_id: 'developer',
          job_metadata: null,
          status: 'processing',
        }),
      ]);
      const titles = component.activeJobs().map((j) => component.resolveTitle(j));
      expect(titles).toEqual([
        'From Metadata A',
        'developer',
        'developer',
      ]);
    });
  });

  describe('jobClick emit', () => {
    beforeEach(() => {
      // Each test starts with a fresh emit spy. The component is
      // re-created in the outer beforeEach, but jest.fn() state on
      // the class field persists across the same instance, so reset
      // explicitly.
      (component.jobClick.emit as jest.Mock).mockClear();
    });

    it('should expose jobClick.emit as a function', () => {
      expect(typeof component.jobClick.emit).toBe('function');
    });

    it('should emit the clicked job once on a single onRowClick', () => {
      const job = createMockJob({ status: 'processing' });
      component.onRowClick(job);
      expect(component.jobClick.emit).toHaveBeenCalledTimes(1);
      expect(component.jobClick.emit).toHaveBeenCalledWith(job);
    });

    it('should emit both jobs in order across successive onRowClick calls', () => {
      const jobA = createMockJob({ job_id: 'job-A', status: 'processing' });
      const jobB = createMockJob({ job_id: 'job-B', status: 'completed' });
      component.onRowClick(jobA);
      component.onRowClick(jobB);
      expect(component.jobClick.emit).toHaveBeenCalledTimes(2);
      expect(component.jobClick.emit).toHaveBeenNthCalledWith(1, jobA);
      expect(component.jobClick.emit).toHaveBeenNthCalledWith(2, jobB);
    });
  });

  // ── Fix C (§8.2) — panel mission awareness ───────────────────────────

  describe('Fix C liveMissionCount + missionChip', () => {
    it('should default liveMissionCount to null (pre-data state)', () => {
      // C3 fix: the panel input is now ``number | null``. Initial
      // state is ``null`` — "count unavailable", NOT 0. A healthy
      // tick with zero live missions is a separate signal (0).
      expect(component.liveMissionCount()).toBeNull();
    });

    it('should accept a live-mission count from the parent (drives header pill + empty state)', () => {
      component.setLiveMissionCount(2);
      expect(component.liveMissionCount()).toBe(2);
      // The template branches on this: >0 → header pill + "Queue is
      // idle · N live missions" empty-state subtitle; 0 or null →
      // neither (the subtitle reads "Queue is currently idle").
    });

    it('missionChip: terminal receipt + live mission returns a live chip (core case)', () => {
      const chip = component.missionChip(createMockLiveMissionReceipt());
      expect(chip).not.toBeNull();
      expect(chip!.live).toBe(true);
      expect(chip!.label).toBe('mission: processing');
    });

    it('missionChip: mission rows and degraded-None rows render nothing extra', () => {
      expect(component.missionChip(createMockJob({ job_type: 'task', mission_liveness: null }))).toBeNull();
      expect(component.missionChip(createMockJob({ job_type: 'message', mission_liveness: null }))).toBeNull();
      expect(component.missionChip(createMockJob())).toBeNull();
    });
  });

  // ── Mission-tree panel (2026-09-07, ``feature/job-queue-mission-tree``) ─

  describe('missionTitle', () => {
    function mkMission(over: Partial<MissionSummary> = {}): MissionSummary {
      return {
        mission_id: 'm-1',
        agent_id: 'leader',
        parent_mission_id: null,
        liveness: 'processing',
        terminal_reason: null,
        epoch: 1,
        linked_jobs: [],
        started_at: '2026-09-07T10:00:00Z',
        last_activity_at: '2026-09-07T10:30:00Z',
        title: null,
        initiative_preview: null,
        ...over,
      };
    }

    it('prefers server-authoritative title', () => {
      expect(component.missionTitle(mkMission({ title: 'Refactor auth' }))).toBe('Refactor auth');
    });

    it('falls back to "agent · timeAgo" when no title', () => {
      const t = component.missionTitle(mkMission());
      expect(t).toMatch(/leader · /);
    });
  });

  describe('tree derivation — liveMissions / queued / recent / recentFlat', () => {
    function mkMission(over: Partial<MissionSummary> = {}): MissionSummary {
      return {
        mission_id: 'm-1',
        agent_id: 'leader',
        parent_mission_id: null,
        liveness: 'processing',
        terminal_reason: null,
        epoch: 1,
        linked_jobs: [],
        started_at: '2026-09-07T10:00:00Z',
        last_activity_at: '2026-09-07T10:30:00Z',
        title: null,
        initiative_preview: null,
        ...over,
      };
    }

    it('groups active jobs under their live mission', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'a', mission_id: 'm-1', status: 'processing' }),
        createMockJob({ job_id: 'b', mission_id: 'm-1', status: 'processing' }),
      ]);
      component.setMissions([mkMission({ mission_id: 'm-1', liveness: 'processing' })]);
      const t = component.tree();
      expect(t.liveMissions).toHaveLength(1);
      expect(t.liveMissions[0].jobs.map((j) => j.job_id).sort()).toEqual(['a', 'b']);
      expect(t.queued).toEqual([]);
    });

    it('routes unattached non-terminal jobs to queued (NEVER hide)', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'attached', mission_id: 'm-1', status: 'processing' }),
        createMockJob({ job_id: 'orphan', mission_id: null, status: 'processing' }),
      ]);
      component.setMissions([mkMission({ mission_id: 'm-1' })]);
      const t = component.tree();
      expect(t.liveMissions[0].jobs).toHaveLength(1);
      expect(t.queued).toHaveLength(1);
      expect(t.queued[0].job_id).toBe('orphan');
    });

    it('routes terminal jobs to recentFlat when no mission matches', () => {
      component.setRecentJobs([
        createMockJob({ job_id: 'matched', mission_id: 'm-done', status: 'completed' }),
        createMockJob({ job_id: 'loose', mission_id: null, status: 'failed' }),
      ]);
      component.setMissions([mkMission({ mission_id: 'm-done', liveness: 'completed' })]);
      const t = component.tree();
      expect(t.recent).toHaveLength(1);
      expect(t.recent[0].jobs[0].job_id).toBe('matched');
      expect(t.recentFlat.map((j) => j.job_id)).toEqual(['loose']);
    });

    it('NEVER hides a job — every input row ends up in exactly one bucket', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'live-matched', mission_id: 'm-live', status: 'processing' }),
        createMockJob({ job_id: 'live-unattached', mission_id: null, status: 'processing' }),
      ]);
      component.setRecentJobs([
        createMockJob({ job_id: 'recent-matched', mission_id: 'm-done', status: 'completed' }),
        createMockJob({ job_id: 'recent-unattached', mission_id: null, status: 'failed' }),
      ]);
      component.setMissions([
        mkMission({ mission_id: 'm-live', liveness: 'processing' }),
        mkMission({ mission_id: 'm-done', liveness: 'completed' }),
      ]);
      const t = component.tree();
      const all = [
        ...t.liveMissions.flatMap((n) => n.jobs),
        ...t.queued,
        ...t.recent.flatMap((n) => n.jobs),
        ...t.recentFlat,
      ].map((j) => j.job_id).sort();
      expect(all).toEqual(['live-matched', 'live-unattached', 'recent-matched', 'recent-unattached']);
    });

    it('handles empty missions input without throwing — falls back to legacy flat layout', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      // missions = [] (default), tree still produces queued + recentFlat
      const t = component.tree();
      expect(t.liveMissions).toEqual([]);
      expect(t.queued).toHaveLength(1);
      expect(t.recent).toEqual([]);
      expect(t.recentFlat).toHaveLength(1);
    });
  });

  describe('shouldAutoExpandLive', () => {
    function mkMission(over: Partial<MissionSummary> = {}): MissionSummary {
      return {
        mission_id: 'm-1',
        agent_id: 'leader',
        parent_mission_id: null,
        liveness: 'processing',
        terminal_reason: null,
        epoch: 1,
        linked_jobs: [],
        started_at: null,
        last_activity_at: null,
        title: null,
        initiative_preview: null,
        ...over,
      };
    }

    it('returns true iff exactly one live mission exists', () => {
      component.setMissions([mkMission({ mission_id: 'm-1' })]);
      expect(component.shouldAutoExpandLive()).toBe(true);
    });

    it('returns false for zero or 2+ live missions', () => {
      component.setMissions([]);
      expect(component.shouldAutoExpandLive()).toBe(false);
      component.setMissions([
        mkMission({ mission_id: 'm-1' }),
        mkMission({ mission_id: 'm-2' }),
      ]);
      expect(component.shouldAutoExpandLive()).toBe(false);
    });
  });

  describe('expansion-state survival across poll refresh (test pin 4)', () => {
    // Headline guarantee — ZERO coverage before this fix: a user's
    // manual expansion must SURVIVE a poll refresh that re-orders
    // the live missions. The expansion set is keyed on ``mission_id``
    // (NOT array index), so a refresh that re-sorts or inserts a new
    // mission earlier in the list doesn't collapse the user's toggle.

    function mkMission(over: Partial<MissionSummary> = {}): MissionSummary {
      return {
        mission_id: 'm-1',
        agent_id: 'leader',
        parent_mission_id: null,
        liveness: 'processing',
        terminal_reason: null,
        epoch: 1,
        linked_jobs: [],
        started_at: '2026-09-07T10:00:00Z',
        last_activity_at: '2026-09-07T10:30:00Z',
        title: null,
        initiative_preview: null,
        ...over,
      };
    }

    it('a user-expanded mission stays expanded after a poll refresh that re-orders it', () => {
      // Initial poll: two live missions in order [m-a, m-b].
      component.setMissions([
        mkMission({ mission_id: 'm-a', last_activity_at: '2026-09-07T10:00:00Z' }),
        mkMission({ mission_id: 'm-b', last_activity_at: '2026-09-07T09:00:00Z' }),
      ]);
      // User expands m-a (index 0 in the tree).
      component.toggleLiveMission('m-a');
      expect(component.isLiveExpanded('m-a')).toBe(true);

      // Next poll: m-b has newer activity and is re-ordered to index 0.
      component.setMissions([
        mkMission({ mission_id: 'm-b', last_activity_at: '2026-09-07T11:00:00Z' }),
        mkMission({ mission_id: 'm-a', last_activity_at: '2026-09-07T10:00:00Z' }),
      ]);
      // The tree now lists m-b first, but m-a is still expanded.
      expect(component.isLiveExpanded('m-a')).toBe(true);
      expect(component.isLiveExpanded('m-b')).toBe(false);
    });

    it('a user-expanded mission stays expanded after a poll refresh that inserts a new live mission before it', () => {
      component.setMissions([
        mkMission({ mission_id: 'm-a' }),
      ]);
      component.toggleLiveMission('m-a');
      expect(component.isLiveExpanded('m-a')).toBe(true);

      // Next poll: a new mission m-c arrives at the head of the list.
      component.setMissions([
        mkMission({ mission_id: 'm-c', last_activity_at: '2026-09-07T11:00:00Z' }),
        mkMission({ mission_id: 'm-a', last_activity_at: '2026-09-07T10:00:00Z' }),
      ]);
      expect(component.isLiveExpanded('m-a')).toBe(true);
      expect(component.isLiveExpanded('m-c')).toBe(false);
    });

    it('toggling a mission off collapses it; the rest of the set is preserved', () => {
      component.setMissions([
        mkMission({ mission_id: 'm-a' }),
        mkMission({ mission_id: 'm-b' }),
      ]);
      component.toggleLiveMission('m-a');
      component.toggleLiveMission('m-b');
      expect(component.expandedLiveMissions().size).toBe(2);

      component.toggleLiveMission('m-a');
      expect(component.isLiveExpanded('m-a')).toBe(false);
      expect(component.isLiveExpanded('m-b')).toBe(true);
      expect(component.expandedLiveMissions().size).toBe(1);

      // Poll refresh with changed ordering — only m-b is expanded.
      component.setMissions([
        mkMission({ mission_id: 'm-b' }),
        mkMission({ mission_id: 'm-a' }),
      ]);
      expect(component.isLiveExpanded('m-a')).toBe(false);
      expect(component.isLiveExpanded('m-b')).toBe(true);
    });
  });

  describe('missions input drives panel rendering', () => {
    it('default empty missions yields no liveMissions tree', () => {
      expect(component.missions()).toEqual([]);
      expect(component.tree().liveMissions).toEqual([]);
    });

    it('setMissions replaces the live list verbatim', () => {
      component.setMissions([
        {
          mission_id: 'm-1',
          agent_id: 'leader',
          parent_mission_id: null,
          liveness: 'processing',
          terminal_reason: null,
          epoch: 1,
          linked_jobs: [],
          started_at: null,
          last_activity_at: null,
          title: null,
          initiative_preview: null,
        },
      ]);
      expect(component.missions().length).toBe(1);
    });
  });

  // ── Template binding seams (source-text pin) ────────────────────────
  //
  // Mirror tests prove the component METHODS exist. These source-text
  // pins prove the TEMPLATE actually wires the bindings up — without
  // them, a typo in the HTML would compile green and silently drop
  // the user's footer click / arrow-key navigation. Same pattern as
  // the indicator's panel-binding seam.

  describe('template binding seams (source-text pin)', () => {
    let templateHtml: string;

    beforeAll(() => {
      // Resolve relative to this spec file.
      const path = require('path');
      const fs = require('fs');
      const specDir = __dirname;
      const htmlPath = path.join(specDir, 'job-queue-panel.component.html');
      templateHtml = fs.readFileSync(htmlPath, 'utf-8');
    });

    it('binds (keydown) on the panel-list to onTreeKeydown — T3 arrow nav', () => {
      expect(templateHtml).toContain('(keydown)="onTreeKeydown($event)"');
    });

    it('binds (click) on the footer to onFooterClick — T1 footer link', () => {
      expect(templateHtml).toContain('(click)="onFooterClick()"');
    });

    it('binds (focus) on the live mission row — T3 focused-state mirroring', () => {
      // Each tree row needs the (focus) handler so the
      // ``focusedItemId`` signal mirrors the browser's actual focus.
      expect(templateHtml).toContain('(focus)="onRowFocus(');
    });

    it('binds [class.focused] on each tree row — T3 visible focus indicator', () => {
      expect(templateHtml).toContain('[class.focused]="isFocusedItem(');
    });
  });

  // ── T1 panel footer — "Open full queue →" link ──────────────────────
  //
  // Acceptance item dropped from the approved design (2026-09-07,
  // mission-tree final gaps). The panel stays DUMB/presentational
  // and emits ``footerClick``; the indicator handles the navigation
  // + menu close (mirror-level, see job-queue-indicator.spec.ts).

  describe('T1: footer activation emits footerClick (panel stays DUMB)', () => {
    beforeEach(() => {
      (component.footerClick.emit as jest.Mock).mockClear();
    });

    it('exposes footerClick.emit as a function', () => {
      expect(typeof component.footerClick.emit).toBe('function');
    });

    it('emits exactly one footerClick on a single onFooterClick', () => {
      component.onFooterClick();
      expect(component.footerClick.emit).toHaveBeenCalledTimes(1);
      expect(component.footerClick.emit).toHaveBeenCalledWith();
    });

    it('emits on every successive onFooterClick call', () => {
      component.onFooterClick();
      component.onFooterClick();
      expect(component.footerClick.emit).toHaveBeenCalledTimes(2);
    });

    it('does NOT mutate the jobClick surface (separate output channel)', () => {
      // The footer is its own output — accidentally piping through
      // ``jobClick.emit(job)`` would surface a bogus navigation to
      // the indicator. Keep the two channels cleanly separated.
      (component.jobClick.emit as jest.Mock).mockClear();
      component.onFooterClick();
      expect(component.jobClick.emit).not.toHaveBeenCalled();
    });
  });

  // ── T3 arrow-key tree navigation ─────────────────────────────────────
  //
  // Pure traversal logic lives in ``models/job.model.ts``;
  // ``nextVisibleItem`` + ``visibleTreeItems`` + ``visibleTreeItemId``
  // are imported and exercised directly. The arrow-key handler
  // (``onTreeKeydown``) is mirrored 1:1 so the key→action map can be
  // pinned without DOM.

  describe('T3: visibleTreeItems — flatten the trees in display order', () => {
    function mkMission(over: Partial<MissionSummary> = {}): MissionSummary {
      return {
        mission_id: 'm-1',
        agent_id: 'leader',
        parent_mission_id: null,
        liveness: 'processing',
        terminal_reason: null,
        epoch: 1,
        linked_jobs: [],
        started_at: '2026-09-07T10:00:00Z',
        last_activity_at: '2026-09-07T10:30:00Z',
        title: null,
        initiative_preview: null,
        ...over,
      };
    }

    it('returns only mission nodes when nothing is expanded', () => {
      component.setMissions([
        mkMission({ mission_id: 'live-a', liveness: 'processing' }),
        mkMission({ mission_id: 'live-b', liveness: 'paused' }),
        mkMission({ mission_id: 'rec-a', liveness: 'completed' }),
      ]);
      const items = component.visibleItems();
      expect(items.length).toBe(3);
      expect(items.map((it) => it.kind)).toEqual(['mission', 'mission', 'mission']);
      expect(items.every((it) => it.kind === 'mission')).toBe(true);
    });

    it('includes child jobs only when the parent mission is expanded', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'live-a', status: 'processing' }),
        createMockJob({ job_id: 'j-2', mission_id: 'live-a', status: 'pending' }),
      ]);
      component.setMissions([
        mkMission({ mission_id: 'live-a', liveness: 'processing' }),
        mkMission({ mission_id: 'live-b', liveness: 'paused' }),
      ]);
      // Collapsed: only mission nodes.
      let items = component.visibleItems();
      expect(items.length).toBe(2);
      // Expand live-a → its two child jobs appear AFTER the parent
      // in the flat list.
      component.toggleLiveMission('live-a');
      items = component.visibleItems();
      expect(items.length).toBe(4);
      expect(items.map((it) => it.kind)).toEqual(['mission', 'job', 'job', 'mission']);
      expect(items.map((it) =>
        it.kind === 'mission' ? it.node.mission.mission_id : it.job.job_id
      )).toEqual(['live-a', 'j-1', 'j-2', 'live-b']);
    });

    it('skips children of COLLAPSED nodes — they are invisible to arrow nav', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'live-a', status: 'processing' }),
        createMockJob({ job_id: 'j-2', mission_id: 'live-b', status: 'processing' }),
      ]);
      component.setMissions([
        mkMission({ mission_id: 'live-a', liveness: 'processing' }),
        mkMission({ mission_id: 'live-b', liveness: 'processing' }),
      ]);
      // Expand ONLY live-a — live-b stays collapsed. live-b's
      // child j-2 must NOT appear in the visible list.
      component.toggleLiveMission('live-a');
      const items = component.visibleItems();
      expect(items.length).toBe(3); // live-a, j-1, live-b
      expect(items.map((it) =>
        it.kind === 'mission' ? it.node.mission.mission_id : it.job.job_id
      )).toEqual(['live-a', 'j-1', 'live-b']);
    });

    it('emits LIVE tree items before RECENT tree items', () => {
      component.setMissions([
        mkMission({ mission_id: 'live-a', liveness: 'processing' }),
        mkMission({ mission_id: 'rec-a', liveness: 'completed' }),
      ]);
      const items = component.visibleItems();
      expect(items[0].tree).toBe('live');
      expect(items[1].tree).toBe('recent');
    });
  });

  describe('T3: nextVisibleItem — clamped boundary behaviour', () => {
    function makeItems(): VisibleTreeItem[] {
      return [
        { kind: 'mission', tree: 'live', node: { mission: { mission_id: 'a' } as MissionSummary, jobs: [] } },
        { kind: 'mission', tree: 'live', node: { mission: { mission_id: 'b' } as MissionSummary, jobs: [] } },
        { kind: 'mission', tree: 'live', node: { mission: { mission_id: 'c' } as MissionSummary, jobs: [] } },
      ];
    }

    it('ArrowDown from index 0 → 1, 1 → 2', () => {
      const items = makeItems();
      expect(nextVisibleItem(items, 0, 1)).toBe(1);
      expect(nextVisibleItem(items, 1, 1)).toBe(2);
    });

    it('ArrowDown clamps at the LAST item (no wrap)', () => {
      const items = makeItems();
      expect(nextVisibleItem(items, 2, 1)).toBe(2);
    });

    it('ArrowUp clamps at the FIRST item (no wrap)', () => {
      const items = makeItems();
      expect(nextVisibleItem(items, 0, -1)).toBe(0);
    });

    it('ArrowDown from -1 (no current focus) lands on the FIRST item', () => {
      const items = makeItems();
      expect(nextVisibleItem(items, -1, 1)).toBe(0);
    });

    it('ArrowUp from -1 (no current focus) lands on the LAST item', () => {
      const items = makeItems();
      expect(nextVisibleItem(items, -1, -1)).toBe(2);
    });

    it('returns -1 for an empty list regardless of direction', () => {
      expect(nextVisibleItem([], 0, 1)).toBe(-1);
      expect(nextVisibleItem([], -1, -1)).toBe(-1);
    });

    it('out-of-range currentIndex (-5 or 99) is treated as no-focus (lands at edge in direction of travel)', () => {
      // ``-5`` is below range, ``99`` is above range — both are
      // collapsed to the "no focus" branch. Down from any
      // out-of-range lands at the first item; Up lands at the last.
      const items = makeItems();
      expect(nextVisibleItem(items, -5, 1)).toBe(0);
      expect(nextVisibleItem(items, -5, -1)).toBe(2);
      expect(nextVisibleItem(items, 99, 1)).toBe(0);
      expect(nextVisibleItem(items, 99, -1)).toBe(2);
    });
  });

  describe('T3: onTreeKeydown — key→action map', () => {
    function mkMission(over: Partial<MissionSummary> = {}): MissionSummary {
      return {
        mission_id: 'm-1',
        agent_id: 'leader',
        parent_mission_id: null,
        liveness: 'processing',
        terminal_reason: null,
        epoch: 1,
        linked_jobs: [],
        started_at: '2026-09-07T10:00:00Z',
        last_activity_at: '2026-09-07T10:30:00Z',
        title: null,
        initiative_preview: null,
        ...over,
      };
    }

    function makeKeyboardEvent(key: string): KeyboardEvent {
      // Minimal KeyboardEvent stub — the handler only reads ``key``
      // and calls ``preventDefault`` + ``stopPropagation``. Both
      // methods exist on the real KeyboardEvent so a plain object
      // with those methods is sufficient for the spec.
      return { key, preventDefault: jest.fn(), stopPropagation: jest.fn() } as unknown as KeyboardEvent;
    }

    it('ArrowDown advances focus to the next visible item', () => {
      component.setMissions([mkMission({ mission_id: 'a' }), mkMission({ mission_id: 'b' })]);
      component.onRowFocus(visibleTreeItemId(component.visibleItems()[0]));
      expect(component.focusedItemId()).toBe('tree:live|mission:a');
      component.onTreeKeydown(makeKeyboardEvent('ArrowDown'));
      expect(component.focusedItemId()).toBe('tree:live|mission:b');
    });

    it('ArrowUp moves focus to the previous visible item', () => {
      component.setMissions([mkMission({ mission_id: 'a' }), mkMission({ mission_id: 'b' })]);
      component.onRowFocus(visibleTreeItemId(component.visibleItems()[1]));
      expect(component.focusedItemId()).toBe('tree:live|mission:b');
      component.onTreeKeydown(makeKeyboardEvent('ArrowUp'));
      expect(component.focusedItemId()).toBe('tree:live|mission:a');
    });

    it('ArrowDown with no current focus (-1) lands on the first item', () => {
      component.setMissions([mkMission({ mission_id: 'a' }), mkMission({ mission_id: 'b' })]);
      component.onTreeKeydown(makeKeyboardEvent('ArrowDown'));
      expect(component.focusedItemId()).toBe('tree:live|mission:a');
    });

    it('ArrowRight expands a collapsed live mission node', () => {
      component.setMissions([mkMission({ mission_id: 'a' })]);
      expect(component.isLiveExpanded('a')).toBe(false);
      component.onRowFocus(visibleTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowRight'));
      expect(component.isLiveExpanded('a')).toBe(true);
    });

    it('ArrowRight is a no-op on an already-expanded mission (no toggle-fight)', () => {
      component.setMissions([mkMission({ mission_id: 'a' })]);
      component.toggleLiveMission('a');
      expect(component.isLiveExpanded('a')).toBe(true);
      component.onRowFocus(visibleTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowRight'));
      // Still expanded (not collapsed by an extra ArrowRight).
      expect(component.isLiveExpanded('a')).toBe(true);
    });

    it('ArrowRight on a JOB CHILD row is a no-op (children have no children of their own)', () => {
      component.setActiveJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'a', status: 'processing' }),
      ]);
      component.setMissions([mkMission({ mission_id: 'a' })]);
      component.toggleLiveMission('a');
      const childItem = component.visibleItems().find((it) => it.kind === 'job');
      expect(childItem).toBeDefined();
      component.onRowFocus(visibleTreeItemId(childItem!));
      // ArrowRight on a job child does nothing.
      component.onTreeKeydown(makeKeyboardEvent('ArrowRight'));
      // The mission is still expanded (no state change).
      expect(component.isLiveExpanded('a')).toBe(true);
    });

    it('ArrowLeft collapses an expanded mission node', () => {
      component.setMissions([mkMission({ mission_id: 'a' })]);
      component.toggleLiveMission('a');
      expect(component.isLiveExpanded('a')).toBe(true);
      component.onRowFocus(visibleTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowLeft'));
      expect(component.isLiveExpanded('a')).toBe(false);
    });

    it('ArrowLeft on a JOB CHILD collapses the parent mission', () => {
      // WAI-ARIA tree pattern: ← on a child collapses the parent so
      // keyboard focus returns to a navigable level.
      component.setActiveJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'a', status: 'processing' }),
      ]);
      component.setMissions([mkMission({ mission_id: 'a' })]);
      component.toggleLiveMission('a');
      expect(component.isLiveExpanded('a')).toBe(true);
      const childItem = component.visibleItems().find((it) => it.kind === 'job');
      expect(childItem).toBeDefined();
      component.onRowFocus(visibleTreeItemId(childItem!));
      component.onTreeKeydown(makeKeyboardEvent('ArrowLeft'));
      expect(component.isLiveExpanded('a')).toBe(false);
    });

    it('ArrowLeft on an already-collapsed node is a no-op', () => {
      component.setMissions([mkMission({ mission_id: 'a' })]);
      expect(component.isLiveExpanded('a')).toBe(false);
      component.onRowFocus(visibleTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowLeft'));
      expect(component.isLiveExpanded('a')).toBe(false);
    });

    it('Enter, Space, Escape, and other keys are NOT handled by the panel arrow handler', () => {
      // Enter/Space stay on the individual rows; Esc already closes
      // the mat-menu. The panel-level handler must NOT swallow them.
      component.setMissions([mkMission({ mission_id: 'a' })]);
      const beforeId = component.focusedItemId();
      const event = makeKeyboardEvent('Enter');
      component.onTreeKeydown(event);
      // No state change — focus id stays the same, no expansion.
      expect(component.focusedItemId()).toBe(beforeId);
      expect(component.isLiveExpanded('a')).toBe(false);
      // preventDefault / stopPropagation were NOT called for an
      // unrelated key (the function returns before reaching them).
      expect(event.preventDefault).not.toHaveBeenCalled();
    });

    it('prevents the default page scroll on ArrowDown/ArrowUp inside the panel', () => {
      component.setMissions([mkMission({ mission_id: 'a' })]);
      const event = makeKeyboardEvent('ArrowDown');
      component.onTreeKeydown(event);
      // The handler calls preventDefault so the page doesn't scroll
      // while the user is navigating the menu.
      expect(event.preventDefault).toHaveBeenCalled();
    });
  });
});
