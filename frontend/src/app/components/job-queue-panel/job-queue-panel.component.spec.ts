import { signal, computed } from '@angular/core';
import {
  getStatusColor as modelGetStatusColor,
  Job,
  JobStatus,
  missionLivenessChip,
} from '../../models/job.model';
import {
  InstanceNode,
  InstanceRow,
  InstanceTreeItem,
  buildInstanceNodes,
  buildInstanceTree,
  shouldAutoExpandInstanceTree,
  visibleInstanceTreeItems,
  nextInstanceTreeItem,
  instanceTreeItemId,
} from '../../models/instance-node.model';
import { createMockJob, createMockLiveMissionReceipt } from '../../testing/job-test-helpers';

/**
 * Logic-mirror of JobQueuePanelComponent (instances-primary tree,
 * 2026-09-08, design V1).
 *
 * This project does NOT use Angular TestBed for component tests —
 * see ``job-queue-indicator.component.spec.ts`` for the same pattern.
 * We replicate the component's signal/computed logic and its helper
 * methods in a plain TS class so the assertions below can exercise
 * tree derivation, expansion survival, capping, formatting, and the
 * key→action map without Angular DI.
 *
 * The mirror delegates tree building + traversal to the REAL model
 * helpers (buildInstanceTree / visibleInstanceTreeItems /
 * nextInstanceTreeItem / instanceTreeItemId) — the same imports the
 * real component uses — so the specs prove the contract against the
 * production derivations, not a local copy.
 */
class MockJobQueuePanelComponent {
  private readonly _instances = signal<InstanceNode[]>([]);
  private readonly _activeJobs = signal<Job[]>([]);
  private readonly _recentJobs = signal<Job[]>([]);
  private readonly _projectNameMap = signal<Map<string | null, string>>(new Map());
  private readonly _liveMissionCount = signal<number | null>(null);

  activeInstances = this._instances.asReadonly();
  activeJobs = this._activeJobs.asReadonly();
  recentJobs = this._recentJobs.asReadonly();
  projectNameMap = this._projectNameMap.asReadonly();
  liveMissionCount = this._liveMissionCount.asReadonly();

  /** Mock outputs — mirror the real component's ``output<T>()``. */
  readonly jobClick = { emit: jest.fn() };
  readonly instanceClick = { emit: jest.fn() };
  readonly footerClick = { emit: jest.fn() };

  /** Tree derivation — mirrors the real component's ``tree`` computed. */
  readonly tree = computed(() =>
    buildInstanceTree(this._instances(), this._activeJobs(), this._recentJobs())
  );

  /**
   * T3 mock — the flattened list of items the arrow-key handler can
   * land on, built from the panel's tree + expansion set (mirrors
   * the real ``visibleItems`` computed).
   */
  readonly visibleItems = computed<InstanceTreeItem[]>(() =>
    visibleInstanceTreeItems(
      this.tree().liveRoots,
      this.tree().recentRoots,
      this._expandedInstances()
    )
  );

  /** Section views — mirror the real component's template filters. */
  readonly liveItems = computed<InstanceTreeItem[]>(() =>
    this.visibleItems().filter((it) => it.tree === 'live')
  );
  readonly recentItems = computed<InstanceTreeItem[]>(() =>
    this.visibleItems().filter((it) => it.tree === 'recent')
  );

  /** T3 mock — the row that owns keyboard focus (drives ``.focused``). */
  private readonly _focusedItemId = signal<string | null>(null);
  readonly focusedItemId = this._focusedItemId.asReadonly();

  isEmpty = computed(() => {
    const t = this.tree();
    const liveCount = this._liveMissionCount();
    return (
      t.liveRoots.length === 0 &&
      t.queued.length === 0 &&
      t.recentRoots.length === 0 &&
      t.recentFlat.length === 0 &&
      (liveCount === null || liveCount === 0)
    );
  });

  activeCount = computed(() => this._activeJobs().length);

  /**
   * Fix C (§8.2) mirror — mission-liveness chip for a receipt row, or
   * null when the row renders nothing extra. UNCHANGED by the
   * instances-primary redesign. Calls the SAME model helper the real
   * component calls.
   */
  missionChip(job: Job) {
    return missionLivenessChip(job);
  }

  setLiveMissionCount(n: number | null): void {
    this._liveMissionCount.set(n);
  }

  /**
   * Expansion-state mirror — keys the expansion set on
   * ``instance_id`` (globally unique, so ONE set serves both trees;
   * NOT array index) so a poll refresh that re-orders the roots does
   * NOT collapse the user's expanded nodes. Mirrors the real
   * component's ``expandedInstances`` signal + ``toggleInstance`` /
   * ``isExpanded``.
   */
  private readonly _expandedInstances = signal<Set<string>>(new Set());

  /**
   * G1 mirror — ids the user has manually toggled. The real
   * component's auto-seed effect filters these out of
   * ``shouldAutoExpandInstanceTree`` and never re-toggles them. The
   * mirror needs the same bookkeeping to drive the G1 pin.
   */
  private readonly _userTouchedInstances = signal<Set<string>>(new Set());

  toggleInstance(instanceId: string): void {
    this._userTouchedInstances.update((s) => {
      if (s.has(instanceId)) return s;
      const next = new Set(s);
      next.add(instanceId);
      return next;
    });
    this._expandedInstances.update((s) => {
      const next = new Set(s);
      if (next.has(instanceId)) next.delete(instanceId);
      else next.add(instanceId);
      return next;
    });
  }

  isExpanded(instanceId: string | null | undefined): boolean {
    if (!instanceId) return false;
    return this._expandedInstances().has(instanceId);
  }

  /** Read-only view — used by the survival test. */
  readonly expandedInstances = this._expandedInstances.asReadonly();

  /** Read-only view — used by the G1 pin (user-touched ids). */
  readonly userTouchedInstances = this._userTouchedInstances.asReadonly();

  /**
   * G1 auto-seed mirror — equivalent to the real component's
   * constructor effect: when the LIVE-root set drifts, auto-seed the
   * UNTOUCHED first-2-live ids, MERGING onto the existing expansion
   * set. NEVER un-toggle or re-expand a user-decided id. The mirror
   * exposes this as a method (instead of an effect) so the test can
   * drive it deterministically across successive live-set changes.
   */
  applyAutoSeed(): void {
    const liveRoots = this.tree().liveRoots;
    const touched = this._userTouchedInstances();
    const seedIds = shouldAutoExpandInstanceTree(liveRoots).filter(
      (id) => !touched.has(id)
    );
    if (seedIds.length === 0) return;
    const current = this._expandedInstances();
    const next = new Set(current);
    let changed = false;
    for (const id of seedIds) {
      if (!next.has(id)) {
        next.add(id);
        changed = true;
      }
    }
    if (changed) this._expandedInstances.set(next);
  }

  /** Mirror of the real read-side ``isFocusedItem``. */
  isFocusedItem(id: string | null | undefined): boolean {
    if (!id) return false;
    return this._focusedItemId() === id;
  }

  /** Mirror of ``onRowFocus`` — the single writer of focusedItemId. */
  onRowFocus(id: string): void {
    this._focusedItemId.set(id);
  }

  /**
   * Mirror of the real ``onTreeKeydown`` 1:1 (minus the real DOM
   * focus call, which has no DOM here — the equivalent
   * ``focusedItemId`` write is applied directly, exactly what the
   * row's ``(focus)`` handler would do after the real focus move).
   * Enter/Space/Esc are NOT handled here (they stay on the rows).
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
    event.preventDefault();
    event.stopPropagation();
    const currentId = this._focusedItemId();
    const currentIndex = currentId
      ? items.findIndex((it) => instanceTreeItemId(it) === currentId)
      : -1;

    if (key === 'ArrowDown' || key === 'ArrowUp') {
      const delta: -1 | 1 = key === 'ArrowUp' ? -1 : 1;
      const nextIndex = nextInstanceTreeItem(items, currentIndex, delta);
      if (nextIndex < 0) return;
      this._focusedItemId.set(instanceTreeItemId(items[nextIndex]));
      return;
    }

    if (currentIndex < 0) return;
    const current = items[currentIndex];

    if (key === 'ArrowRight') {
      if (current.kind !== 'instance') return; // job children: no-op
      const id = current.node.instance.instance_id;
      if (!this.isExpanded(id)) this.toggleInstance(id);
      return;
    }

    // ArrowLeft: collapse if expanded; on a child row (job OR child
    // instance), collapse the nearest ancestor and refocus it
    // (WAI-ARIA tree pattern).
    if (key === 'ArrowLeft') {
      const selfId =
        current.kind === 'instance' ? current.node.instance.instance_id : null;
      const targetId =
        current.kind === 'instance'
          ? (current.parentInstanceId ?? selfId ?? '')
          : (current.parentInstanceId ?? '');
      if (!targetId) return;
      if (!this.isExpanded(targetId)) return;
      this.toggleInstance(targetId);
      if (selfId === null || targetId !== selfId) {
        const ancestor = this.findNodeById(targetId);
        if (!ancestor) return;
        this._focusedItemId.set(
          instanceTreeItemId({
            kind: 'instance',
            tree: current.tree,
            depth: 0,
            parentInstanceId: null,
            node: ancestor,
          })
        );
      }
    }
  }

  /** Mirror of the real defensive node lookup (refocus after collapse). */
  private findNodeById(id: string): InstanceNode | undefined {
    const walk = (nodes: readonly InstanceNode[]): InstanceNode | undefined => {
      for (const n of nodes) {
        if (n.instance.instance_id === id) return n;
        const found = walk(n.children);
        if (found) return found;
      }
      return undefined;
    };
    return walk(this.tree().liveRoots) ?? walk(this.tree().recentRoots);
  }

  /** Mirror — instance row activation (ROW CLICK = NAVIGATE). */
  onInstanceRowClick(node: InstanceNode): void {
    this.instanceClick.emit(node);
  }

  /**
   * Mirror of ``onChevronClick`` — the chevron is the ONLY expand
   * toggle and must stop propagation so the row's navigate-click
   * never fires.
   */
  onChevronClick(event: Event, instanceId: string): void {
    event.stopPropagation();
    this.toggleInstance(instanceId);
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

  getStatusIcon(status: JobStatus): string {
    switch (status) {
      case 'completed':
        return 'check_circle';
      case 'settled':
        // Receipt-style glyph — a settled mirror row IS a delivery receipt,
        // not a completed mission. `receipt_long` (Material Icons codepoint
        // ef6e) is visually distinct from completed's check_circle.
        return 'receipt_long';
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

  // ── Test helpers — mirror the writable inputs ──────────────────────

  /**
   * Replace the instance roots. Accepts FLAT rows (the wire shape)
   * and nests them via the REAL ``buildInstanceNodes`` — the same
   * chain the indicator → panel uses end-to-end.
   */
  setInstances(rows: InstanceRow[]): void {
    this._instances.set(buildInstanceNodes(rows));
  }

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

/** Wire-row factory mirroring ``GET /api/instances`` rows. */
function mkInstance(over: Partial<InstanceRow> = {}): InstanceRow {
  return {
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
}

describe('JobQueuePanelComponent Logic', () => {
  let component: MockJobQueuePanelComponent;

  beforeEach(() => {
    component = new MockJobQueuePanelComponent();
  });

  describe('instantiation', () => {
    it('should create the logic-mirror component', () => {
      expect(component).toBeDefined();
    });

    it('should default to empty state and 0 active jobs', () => {
      expect(component.isEmpty()).toBe(true);
      expect(component.activeCount()).toBe(0);
    });
  });

  describe('resolveTitle priority chain', () => {
    it('should prefer job_metadata.instance_name over agent_id', () => {
      const job = createMockJob({
        agent_id: 'worker',
        job_metadata: { instance_name: 'My Instance' },
      });
      expect(component.resolveTitle(job)).toBe('My Instance');
    });

    it('should fall back to agent_id when no metadata', () => {
      const job = createMockJob({ agent_id: 'worker' });
      expect(component.resolveTitle(job)).toBe('worker');
    });

    it('should fall back to shortened id when nothing else is available', () => {
      const job = createMockJob({ agent_id: '', instance_id: '12345678-abcd' });
      expect(component.resolveTitle(job)).toBe('12345678...');
    });

    it('should skip empty instance_name in metadata', () => {
      const job = createMockJob({
        agent_id: 'worker',
        job_metadata: { instance_name: '' },
      });
      expect(component.resolveTitle(job)).toBe('worker');
    });

    it('should fall back to job_id when instance_id is null and agent_id is empty', () => {
      const job = createMockJob({ agent_id: '', instance_id: null, job_id: 'job-xyz-12345' });
      expect(component.resolveTitle(job)).toBe('job-xyz-...');
    });
  });

  describe('shortenId', () => {
    it('should truncate ids longer than 8 chars with "..."', () => {
      expect(component.shortenId('abcdefghijk')).toBe('abcdefgh...');
    });

    it('should not truncate ids that are exactly 8 chars', () => {
      expect(component.shortenId('abcdefgh')).toBe('abcdefgh');
    });

    it('should not truncate ids shorter than 8 chars', () => {
      expect(component.shortenId('abc')).toBe('abc');
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
      component.setProjectNameMap(new Map([['p1', 'My Project']]));
      const job = createMockJob({ project_id: 'p1' });
      expect(component.projectLabel(job)).toBe('My Project');
    });

    it('should fall back to shortened id when name is missing', () => {
      const job = createMockJob({ project_id: 'p123456789' });
      expect(component.projectLabel(job)).toBe('p1234567...');
    });

    it('should return em-dash for null project_id', () => {
      const job = createMockJob({ project_id: null });
      expect(component.projectLabel(job)).toBe('—');
    });
  });

  describe('timeAgo', () => {
    it('should return "just now" for timestamps within the last minute', () => {
      const now = new Date();
      expect(component.timeAgo(now.toISOString())).toBe('just now');
    });

    it('should return "Xm ago" for minutes', () => {
      const d = new Date(Date.now() - 5 * 60_000);
      expect(component.timeAgo(d.toISOString())).toBe('5m ago');
    });

    it('should return "Xh ago" for hours', () => {
      const d = new Date(Date.now() - 3 * 3_600_000);
      expect(component.timeAgo(d.toISOString())).toBe('3h ago');
    });

    it('should return "Xd ago" for days', () => {
      const d = new Date(Date.now() - 2 * 86_400_000);
      expect(component.timeAgo(d.toISOString())).toBe('2d ago');
    });

    it('should return empty string for null', () => {
      expect(component.timeAgo(null)).toBe('');
    });

    it('should return empty string for undefined', () => {
      expect(component.timeAgo(undefined)).toBe('');
    });

    it('should return a locale date for items older than 7 days', () => {
      const d = new Date(Date.now() - 10 * 86_400_000);
      expect(component.timeAgo(d.toISOString())).toBe(d.toLocaleDateString());
    });
  });

  describe('isEmpty (instances-primary tree)', () => {
    it('should be true when there are no roots and no jobs', () => {
      expect(component.isEmpty()).toBe(true);
    });

    it('should be false when a live root exists', () => {
      component.setInstances([mkInstance({ instance_id: 'i-live', status: 'running' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when only a terminal root exists', () => {
      component.setInstances([mkInstance({ instance_id: 'i-done', status: 'completed' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when activeJobs has unattached items', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when recentJobs has unattached items', () => {
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.isEmpty()).toBe(false);
    });

    it('should be false when a positive liveMissionCount is retained across a degraded tick', () => {
      expect(component.isEmpty()).toBe(true);
      component.setLiveMissionCount(2);
      expect(component.isEmpty()).toBe(false);
      component.setLiveMissionCount(null);
      expect(component.isEmpty()).toBe(true);
    });
  });

  describe('activeCount', () => {
    it('should reflect the number of active jobs (running + pending + paused)', () => {
      component.setActiveJobs([
        createMockJob({ status: 'processing' }),
        createMockJob({ status: 'pending' }),
        createMockJobWithStatusLike('paused'),
      ]);
      expect(component.activeCount()).toBe(3);
    });

    it('should not count recent jobs', () => {
      component.setActiveJobs([createMockJob({ status: 'processing' })]);
      component.setRecentJobs([createMockJob({ status: 'completed' })]);
      expect(component.activeCount()).toBe(1);
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

    it('should map settled to receipt_long — SPEC PIN (receipt glyph)', () => {
      // A settled mirror row IS a delivery receipt — it previously fell to
      // the default info glyph (user-visible defect). `receipt_long`
      // (Material Icons codepoint ef6e) marks it receipt-style.
      expect(component.getStatusIcon('settled')).toBe('receipt_long');
    });

    it('settled receipt_long must be visually distinct from completed check_circle', () => {
      expect(component.getStatusIcon('settled')).not.toBe(component.getStatusIcon('completed'));
      expect(component.getStatusIcon('settled')).not.toBe('info');
      expect(component.getStatusIcon('completed')).toBe('check_circle');
    });

    it('should fall back to info/grey for non-terminal statuses', () => {
      expect(component.getStatusIcon('processing')).toBe('info');
      expect(component.getStatusColor('processing')).toBe('#3B82F6');
    });
  });

  describe('Fix C missionChip (receipt rows — unchanged by the redesign)', () => {
    it('should default liveMissionCount to null (pre-data state)', () => {
      expect(component.liveMissionCount()).toBeNull();
    });

    it('should surface the chip on a live mission receipt', () => {
      const receipt = createMockLiveMissionReceipt({ mission_liveness: 'processing' });
      const chip = component.missionChip(receipt);
      expect(chip).not.toBeNull();
      expect(chip?.live).toBe(true);
    });

    it('should render nothing extra on task rows', () => {
      const task = createMockJob({ status: 'completed', job_type: 'task' });
      expect(component.missionChip(task)).toBeNull();
    });
  });

  describe('jobClick emit', () => {
    it('should expose jobClick.emit as a function', () => {
      expect(typeof component.jobClick.emit).toBe('function');
    });

    it('should emit the clicked job once on a single onRowClick', () => {
      const job = createMockJob({ job_id: 'j-1' });
      component.onRowClick(job);
      expect(component.jobClick.emit).toHaveBeenCalledTimes(1);
      expect(component.jobClick.emit).toHaveBeenCalledWith(job);
    });

    it('should emit both jobs in order across successive onRowClick calls', () => {
      const j1 = createMockJob({ job_id: 'j-1' });
      const j2 = createMockJob({ job_id: 'j-2' });
      component.onRowClick(j1);
      component.onRowClick(j2);
      expect(component.jobClick.emit).toHaveBeenNthCalledWith(1, j1);
      expect(component.jobClick.emit).toHaveBeenNthCalledWith(2, j2);
    });
  });

  describe('instanceClick emit — ROW CLICK = NAVIGATE (design V1)', () => {
    it('should emit the node on an instance row click (root)', () => {
      component.setInstances([mkInstance({ instance_id: 'i-root' })]);
      const node = component.tree().liveRoots[0];
      component.onInstanceRowClick(node);
      expect(component.instanceClick.emit).toHaveBeenCalledTimes(1);
      expect(component.instanceClick.emit).toHaveBeenCalledWith(node);
    });

    it('should emit the node for a CHILD instance row too', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-root', children: ['i-child'] }),
        mkInstance({ instance_id: 'i-child', parent_id: 'i-root', status: 'running' }),
      ]);
      component.toggleInstance('i-root');
      const childNode = component.tree().liveRoots[0].children[0];
      component.onInstanceRowClick(childNode);
      expect(component.instanceClick.emit).toHaveBeenCalledWith(childNode);
    });

    it('should NOT touch jobClick (separate output channel)', () => {
      component.setInstances([mkInstance({ instance_id: 'i-root' })]);
      component.onInstanceRowClick(component.tree().liveRoots[0]);
      expect(component.jobClick.emit).not.toHaveBeenCalled();
    });

    it('keymap Enter on an instance row routes through instanceClick (navigate)', () => {
      // The template binds (keydown.enter) on instance rows to the
      // SAME onInstanceRowClick handler as (click) — the keymap's
      // Enter = navigate on instance nodes. Mirror-level: activating
      // the row emits instanceClick with the node.
      component.setInstances([mkInstance({ instance_id: 'i-root' })]);
      const node = component.tree().liveRoots[0];
      component.onInstanceRowClick(node);
      expect(component.instanceClick.emit).toHaveBeenCalledWith(node);
    });
  });

  describe('tree derivation — liveRoots / queued / recentRoots / recentFlat', () => {
    it('attaches a job to its ROOT instance node via mission_id', () => {
      component.setInstances([mkInstance({ instance_id: 'i-a', status: 'running' })]);
      component.setActiveJobs([createMockJob({ job_id: 'j-1', mission_id: 'i-a', status: 'processing' })]);
      const tree = component.tree();
      expect(tree.liveRoots.length).toBe(1);
      expect(tree.liveRoots[0].attachedJobs.map((j) => j.job_id)).toEqual(['j-1']);
      expect(tree.queued).toEqual([]);
    });

    it('attaches a job to a CHILD instance node at depth (child first, jobs under the subtree)', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-root', status: 'running', children: ['i-child'] }),
        mkInstance({ instance_id: 'i-child', parent_id: 'i-root', agent_id: 'worker', status: 'running' }),
      ]);
      component.setActiveJobs([createMockJob({ job_id: 'j-1', mission_id: 'i-child', status: 'processing' })]);
      const tree = component.tree();
      const child = tree.liveRoots[0].children[0];
      expect(child.instance.instance_id).toBe('i-child');
      expect(child.attachedJobs.map((j) => j.job_id)).toEqual(['j-1']);
      // The root does NOT absorb the child's job.
      expect(tree.liveRoots[0].attachedJobs).toEqual([]);
    });

    it('routes an unattached NON-TERMINAL job to queued (NEVER hide)', () => {
      component.setInstances([mkInstance({ instance_id: 'i-a', status: 'running' })]);
      component.setActiveJobs([createMockJob({ job_id: 'j-orph', mission_id: 'i-missing', status: 'pending' })]);
      const tree = component.tree();
      expect(tree.queued.map((j) => j.job_id)).toEqual(['j-orph']);
    });

    it('routes an unattached TERMINAL job to recentFlat (NEVER hide)', () => {
      component.setInstances([mkInstance({ instance_id: 'i-a', status: 'completed' })]);
      component.setRecentJobs([createMockJob({ job_id: 'j-term', mission_id: 'i-missing', status: 'failed' })]);
      const tree = component.tree();
      expect(tree.recentFlat.map((j) => j.job_id)).toEqual(['j-term']);
    });

    it('keeps a root with a live DESCENDANT in liveRoots even when the root itself is terminal', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-root', status: 'completed', children: ['i-child'] }),
        mkInstance({ instance_id: 'i-child', parent_id: 'i-root', status: 'running' }),
      ]);
      const tree = component.tree();
      expect(tree.liveRoots.map((n) => n.instance.instance_id)).toEqual(['i-root']);
      expect(tree.recentRoots).toEqual([]);
    });

    it('routes a root to recentRoots only when it AND all descendants are terminal', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-root', status: 'completed', children: ['i-child'] }),
        mkInstance({ instance_id: 'i-child', parent_id: 'i-root', status: 'terminated' }),
      ]);
      const tree = component.tree();
      expect(tree.liveRoots).toEqual([]);
      expect(tree.recentRoots.map((n) => n.instance.instance_id)).toEqual(['i-root']);
    });

    it('JOB-row band ≤ MAX + every-job-surfaces: partial fit keeps fitting jobs, overflow spills to recentFlat', () => {
      // Terminal root with 12 attached jobs → 10 jobs fit (JOB-row
      // band = MAX); the remaining 2 overflow into recentFlat
      // (NEVER-hide). Headers ride along — the root's own header is
      // free structure, no capacity consumed.
      component.setInstances([mkInstance({ instance_id: 'i-big', status: 'completed' })]);
      const jobs = Array.from({ length: 12 }, (_, k) =>
        createMockJob({ job_id: `j-${k}`, mission_id: 'i-big', status: 'completed' })
      );
      component.setRecentJobs(jobs);
      const tree = component.tree();
      expect(tree.recentRoots.length).toBe(1);
      const visible = tree.recentRoots[0].attachedJobs.length;
      // JOB-row band = MAX (10 fit), headers ride along.
      expect(visible).toBe(10);
      // Every job surfaces — 2 overflow into recentFlat.
      expect(tree.recentFlat.length).toBe(2);
    });

    it('JOB-row band ≤ MAX: orphan flat rows fill remaining capacity, then overflow appends after', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-1', status: 'completed' }),
        mkInstance({ instance_id: 'i-2', status: 'failed' }),
      ]);
      const nodeJobs = [
        createMockJob({ job_id: 'jn-1', mission_id: 'i-1', status: 'completed' }),
      ];
      const flatJobs = Array.from({ length: 12 }, (_, k) =>
        createMockJob({ job_id: `jf-${k}`, mission_id: null, status: 'cancelled' })
      );
      component.setRecentJobs([...flatJobs, ...nodeJobs]);
      const tree = component.tree();
      // JOB-row band: jn-1 (1) + 9 in-band flat = 10 (≤ MAX). The
      // remaining 3 flat jobs overflow — 2 node headers ride along
      // (free structure). total rendered = 1 + 1 + 9 + 3 = 14.
      const nodeVisible = tree.recentRoots.reduce((s, n) => s + n.attachedJobs.length, 0);
      expect(nodeVisible).toBe(1); // jn-1 on i-1, i-2 contributes 0
      expect(tree.recentFlat.length).toBe(12); // 9 in-band + 3 overflow
      // Nothing lost: every job surfaces (1 in-band + 12 flat).
      const totalSurfaced = nodeVisible + tree.recentFlat.length;
      expect(totalSurfaced).toBe(13); // 1 jn-1 + 12 flat
    });

    it('sorts liveRoots pinned-first then activity desc (instance-list pattern)', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-old', updated_at: '2026-09-08T08:00:00Z' }),
        mkInstance({ instance_id: 'i-new', updated_at: '2026-09-08T11:00:00Z' }),
        mkInstance({
          instance_id: 'i-pin',
          updated_at: '2026-09-08T07:00:00Z',
          pinned: true,
          pinned_at: '2026-09-08T09:00:00Z',
        }),
      ]);
      const ids = component.tree().liveRoots.map((n) => n.instance.instance_id);
      expect(ids).toEqual(['i-pin', 'i-new', 'i-old']);
    });
  });

  describe('auto-expand — first 2 live roots (user-locked: exactly 2)', () => {
    it('shouldAutoExpandInstanceTree returns the FIRST 2 live root ids', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-1', status: 'running' }),
        mkInstance({ instance_id: 'i-2', status: 'running' }),
        mkInstance({ instance_id: 'i-3', status: 'running' }),
      ]);
      const liveRoots = component.tree().liveRoots;
      expect(shouldAutoExpandInstanceTree(liveRoots)).toEqual(['i-1', 'i-2']);
    });

    it('returns fewer ids when fewer live roots exist (and [] when none)', () => {
      component.setInstances([mkInstance({ instance_id: 'i-only', status: 'running' })]);
      expect(shouldAutoExpandInstanceTree(component.tree().liveRoots)).toEqual(['i-only']);
      component.setInstances([mkInstance({ instance_id: 'i-term', status: 'completed' })]);
      expect(shouldAutoExpandInstanceTree(component.tree().liveRoots)).toEqual([]);
    });

    it('terminal roots are NEVER auto-expanded (not part of liveRoots)', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-live', status: 'running' }),
        mkInstance({ instance_id: 'i-term', status: 'completed' }),
      ]);
      const seeds = shouldAutoExpandInstanceTree(component.tree().liveRoots);
      expect(seeds).toEqual(['i-live']);
      expect(seeds).not.toContain('i-term');
    });
  });

  describe('expansion-state survival across poll refresh (keyed by instance_id)', () => {
    it('user expansion survives a poll that re-orders the roots', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-a', updated_at: '2026-09-08T08:00:00Z' }),
        mkInstance({ instance_id: 'i-b', updated_at: '2026-09-08T09:00:00Z' }),
      ]);
      component.toggleInstance('i-a');
      expect(component.isExpanded('i-a')).toBe(true);
      // Poll refresh: i-a jumps to the top (newer activity).
      component.setInstances([
        mkInstance({ instance_id: 'i-a', updated_at: '2026-09-08T12:00:00Z' }),
        mkInstance({ instance_id: 'i-b', updated_at: '2026-09-08T09:00:00Z' }),
      ]);
      expect(component.isExpanded('i-a')).toBe(true);
      expect(component.isExpanded('i-b')).toBe(false);
    });

    it('toggle is idempotent per id and reversible', () => {
      component.toggleInstance('i-a');
      expect(component.expandedInstances().size).toBe(1);
      component.toggleInstance('i-a');
      expect(component.expandedInstances().size).toBe(0);
    });

    it('one set serves both trees — a RECENT node is expandable by the user', () => {
      component.setInstances([mkInstance({ instance_id: 'i-term', status: 'completed' })]);
      expect(component.isExpanded('i-term')).toBe(false); // starts collapsed
      component.toggleInstance('i-term');
      expect(component.isExpanded('i-term')).toBe(true);
      const recentItems = component.visibleItems().filter((it) => it.tree === 'recent');
      // Expanded recent root: node + its child jobs become visible.
      component.setRecentJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'i-term', status: 'completed' }),
      ]);
      const items = component.visibleItems().filter((it) => it.tree === 'recent');
      expect(items.length).toBe(2);
      expect(items.map((it) => it.kind)).toEqual(['instance', 'job']);
    });
  });

  // G1 (2026-09-08 review fold) — auto-seed must NEVER clobber a
  // user-decided expansion. Track user-touched ids in a second set;
  // auto-seed only seeds UNTOUCHED first-2-live ids and MERGES onto
  // the existing expansion set instead of replacing it. User choices
  // survive any subsequent live-set drift.
  describe('G1 — auto-seed clobber fix: user decisions survive live-set drift', () => {
    it('G1: user expansion of a non-top-2 root SURVIVES a new live root arriving', () => {
      // Initial state: 2 live roots → auto-seed would expand i-a, i-b.
      component.setInstances([
        mkInstance({ instance_id: 'i-a', status: 'running', updated_at: '2026-09-08T08:00:00Z' }),
        mkInstance({ instance_id: 'i-b', status: 'running', updated_at: '2026-09-08T09:00:00Z' }),
      ]);
      component.applyAutoSeed();
      expect(component.isExpanded('i-a')).toBe(true);
      expect(component.isExpanded('i-b')).toBe(true);
      // User collapses an auto-seeded root (i-b) AND expands a non-
      // top-2 root (i-c will arrive in the next poll).
      component.toggleInstance('i-b'); // user collapse
      expect(component.isExpanded('i-b')).toBe(false);
      // New poll: i-c arrives as a 3rd live root.
      component.setInstances([
        mkInstance({ instance_id: 'i-a', status: 'running', updated_at: '2026-09-08T08:00:00Z' }),
        mkInstance({ instance_id: 'i-b', status: 'running', updated_at: '2026-09-08T09:00:00Z' }),
        mkInstance({ instance_id: 'i-c', status: 'running', updated_at: '2026-09-08T10:00:00Z' }),
      ]);
      // User expands i-c (non-top-2 — i-c is 3rd by activity).
      component.toggleInstance('i-c');
      expect(component.isExpanded('i-c')).toBe(true);
      // Poll again with a NEW live root (i-d) — user decisions on i-b
      // (collapsed) and i-c (expanded) MUST survive verbatim.
      component.setInstances([
        mkInstance({ instance_id: 'i-a', status: 'running', updated_at: '2026-09-08T08:00:00Z' }),
        mkInstance({ instance_id: 'i-b', status: 'running', updated_at: '2026-09-08T09:00:00Z' }),
        mkInstance({ instance_id: 'i-c', status: 'running', updated_at: '2026-09-08T10:00:00Z' }),
        mkInstance({ instance_id: 'i-d', status: 'running', updated_at: '2026-09-08T11:00:00Z' }),
      ]);
      component.applyAutoSeed();
      // Auto-seed runs, but UNTOUCHED-only — i-b and i-c are
      // user-touched, so the auto-seed skips them.
      expect(component.isExpanded('i-b')).toBe(false); // user-collapsed, NOT re-seeded
      expect(component.isExpanded('i-c')).toBe(true); // user-expanded, NOT re-seeded
      // First-2-live roots are auto-expanded (a, d — sorted by activity).
      expect(component.isExpanded('i-d')).toBe(true);
      expect(component.isExpanded('i-a')).toBe(true);
      // Touched set is exactly the user decisions.
      expect(component.userTouchedInstances().has('i-b')).toBe(true);
      expect(component.userTouchedInstances().has('i-c')).toBe(true);
      expect(component.userTouchedInstances().has('i-a')).toBe(false);
      expect(component.userTouchedInstances().has('i-d')).toBe(false);
    });

    it('G1: auto-seed MERGES onto the existing expansion set (never replaces)', () => {
      // Start with 1 live root + 1 recent root. User expanded i-pre
      // (a non-live recent root).
      component.setInstances([
        mkInstance({ instance_id: 'i-pre', status: 'completed' }),
      ]);
      component.toggleInstance('i-pre'); // user-expanded a recent root
      expect(component.isExpanded('i-pre')).toBe(true);
      // Poll refresh: 2 NEW live roots arrive.
      component.setInstances([
        mkInstance({ instance_id: 'i-pre', status: 'completed' }),
        mkInstance({ instance_id: 'i-a', status: 'running', updated_at: '2026-09-08T10:00:00Z' }),
        mkInstance({ instance_id: 'i-b', status: 'running', updated_at: '2026-09-08T11:00:00Z' }),
      ]);
      component.applyAutoSeed();
      // User-decided i-pre survives (merge, not replace).
      expect(component.isExpanded('i-pre')).toBe(true);
      // First-2-live get auto-seeded alongside.
      expect(component.isExpanded('i-a')).toBe(true);
      expect(component.isExpanded('i-b')).toBe(true);
    });

    it('G1: toggleInstance marks the id as user-touched exactly once', () => {
      component.setInstances([mkInstance({ instance_id: 'i-a', status: 'running' })]);
      expect(component.userTouchedInstances().has('i-a')).toBe(false);
      component.toggleInstance('i-a');
      expect(component.userTouchedInstances().has('i-a')).toBe(true);
      const sizeAfterFirst = component.userTouchedInstances().size;
      component.toggleInstance('i-a');
      component.toggleInstance('i-a');
      // Idempotent — same size regardless of how many toggles.
      expect(component.userTouchedInstances().size).toBe(sizeAfterFirst);
    });

    it('G1: first-2-live auto-seed expansion does NOT mark ids as user-touched', () => {
      component.setInstances([
        mkInstance({ instance_id: 'i-a', status: 'running' }),
        mkInstance({ instance_id: 'i-b', status: 'running' }),
      ]);
      component.applyAutoSeed();
      expect(component.isExpanded('i-a')).toBe(true);
      expect(component.isExpanded('i-b')).toBe(true);
      // Auto-seed is system-driven — these are NOT user decisions.
      expect(component.userTouchedInstances().size).toBe(0);
    });
  });

  describe('visibleItems — flatten the trees in display order', () => {
    it('returns only instance nodes when nothing is expanded', () => {
      component.setInstances([
        mkInstance({ instance_id: 'live-a', status: 'running' }),
        mkInstance({ instance_id: 'live-b', status: 'paused' }),
        mkInstance({ instance_id: 'rec-a', status: 'completed' }),
      ]);
      const items = component.visibleItems();
      expect(items.length).toBe(3);
      expect(items.every((it) => it.kind === 'instance')).toBe(true);
    });

    it('includes child instances then attached jobs only when the node is expanded', () => {
      component.setInstances([
        mkInstance({ instance_id: 'live-a', status: 'running', children: ['child-1'] }),
        mkInstance({ instance_id: 'child-1', parent_id: 'live-a', agent_id: 'worker', status: 'running' }),
        mkInstance({ instance_id: 'live-b', status: 'running' }),
      ]);
      component.setActiveJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'live-a', status: 'processing' }),
      ]);
      // Collapsed: only the two root nodes.
      let items = component.visibleItems();
      expect(items.map((it) => it.kind)).toEqual(['instance', 'instance']);
      // Expand live-a → child instance first, then its receipt job.
      component.toggleInstance('live-a');
      items = component.visibleItems();
      expect(items.map((it) => it.kind)).toEqual(['instance', 'instance', 'job', 'instance']);
      const ids = items.map((it) =>
        it.kind === 'instance' ? it.node.instance.instance_id : it.job.job_id
      );
      expect(ids).toEqual(['live-a', 'child-1', 'j-1', 'live-b']);
    });

    it('depths indent per level (root 0, child 1, receipt under child 2)', () => {
      component.setInstances([
        mkInstance({ instance_id: 'root', status: 'running', children: ['child'] }),
        mkInstance({ instance_id: 'child', parent_id: 'root', status: 'running' }),
      ]);
      component.setActiveJobs([
        createMockJob({ job_id: 'j-1', mission_id: 'child', status: 'processing' }),
      ]);
      component.toggleInstance('root');
      component.toggleInstance('child');
      const items = component.visibleItems();
      expect(items.map((it) => it.depth)).toEqual([0, 1, 2]);
    });

    it('skips descendants of COLLAPSED nodes — invisible to arrow nav', () => {
      component.setInstances([
        mkInstance({ instance_id: 'live-a', status: 'running', children: ['child'] }),
        mkInstance({ instance_id: 'child', parent_id: 'live-a', status: 'running' }),
        mkInstance({ instance_id: 'live-b', status: 'running' }),
      ]);
      component.setActiveJobs([
        createMockJob({ job_id: 'j-child', mission_id: 'child', status: 'processing' }),
      ]);
      // Only live-b expanded — live-a's whole subtree stays hidden.
      component.toggleInstance('live-b');
      const items = component.visibleItems();
      const ids = items.map((it) =>
        it.kind === 'instance' ? it.node.instance.instance_id : it.job.job_id
      );
      expect(ids).toEqual(['live-a', 'live-b']);
      expect(ids).not.toContain('child');
      expect(ids).not.toContain('j-child');
    });

    it('emits LIVE tree items before RECENT tree items', () => {
      component.setInstances([
        mkInstance({ instance_id: 'live-a', status: 'running' }),
        mkInstance({ instance_id: 'rec-a', status: 'completed' }),
      ]);
      const items = component.visibleItems();
      expect(items[0].tree).toBe('live');
      expect(items[1].tree).toBe('recent');
    });
  });

  describe('nextInstanceTreeItem — clamped boundary behaviour', () => {
    function makeItems(): InstanceTreeItem[] {
      const node = (id: string): InstanceNode => ({
        instance: mkInstance({ instance_id: id }),
        children: [],
        attachedJobs: [],
      });
      return [
        { kind: 'instance', tree: 'live', node: node('a'), depth: 0, parentInstanceId: null },
        { kind: 'instance', tree: 'live', node: node('b'), depth: 0, parentInstanceId: null },
        { kind: 'instance', tree: 'live', node: node('c'), depth: 0, parentInstanceId: null },
      ];
    }

    it('ArrowDown from index 0 → 1, 1 → 2', () => {
      const items = makeItems();
      expect(nextInstanceTreeItem(items, 0, 1)).toBe(1);
      expect(nextInstanceTreeItem(items, 1, 1)).toBe(2);
    });

    it('ArrowDown clamps at the LAST item (no wrap)', () => {
      expect(nextInstanceTreeItem(makeItems(), 2, 1)).toBe(2);
    });

    it('ArrowUp clamps at the FIRST item (no wrap)', () => {
      expect(nextInstanceTreeItem(makeItems(), 0, -1)).toBe(0);
    });

    it('ArrowDown from -1 (no current focus) lands on the FIRST item', () => {
      expect(nextInstanceTreeItem(makeItems(), -1, 1)).toBe(0);
    });

    it('ArrowUp from -1 (no current focus) lands on the LAST item', () => {
      expect(nextInstanceTreeItem(makeItems(), -1, -1)).toBe(2);
    });

    it('returns -1 for an empty list regardless of direction', () => {
      expect(nextInstanceTreeItem([], 0, 1)).toBe(-1);
      expect(nextInstanceTreeItem([], -1, -1)).toBe(-1);
    });
  });

  describe('instanceTreeItemId — stable row ids', () => {
    it('encodes an instance node as "inst:<tree>|inst:<id>"', () => {
      const node: InstanceNode = {
        instance: mkInstance({ instance_id: 'i-1' }),
        children: [],
        attachedJobs: [],
      };
      const item: InstanceTreeItem = { kind: 'instance', tree: 'live', node, depth: 0, parentInstanceId: null };
      expect(instanceTreeItemId(item)).toBe('inst:live|inst:i-1');
    });

    it('encodes a job child as "inst:<tree>|inst:<iid>|job:<job_id>"', () => {
      const item: InstanceTreeItem = {
        kind: 'job',
        tree: 'recent',
        instanceId: 'i-1',
        depth: 1,
        parentInstanceId: 'i-1',
        job: createMockJob({ job_id: 'j-9' }),
      };
      expect(instanceTreeItemId(item)).toBe('inst:recent|inst:i-1|job:j-9');
    });

    it('keeps LIVE and RECENT ids independent (no focus bleed)', () => {
      const node: InstanceNode = {
        instance: mkInstance({ instance_id: 'i-1' }),
        children: [],
        attachedJobs: [],
      };
      const live = instanceTreeItemId({ kind: 'instance', tree: 'live', node, depth: 0, parentInstanceId: null });
      const recent = instanceTreeItemId({ kind: 'instance', tree: 'recent', node, depth: 0, parentInstanceId: null });
      expect(live).not.toBe(recent);
    });
  });

  describe('onTreeKeydown — key→action map', () => {
    function makeKeyboardEvent(key: string): KeyboardEvent {
      return { key, preventDefault: jest.fn(), stopPropagation: jest.fn() } as unknown as KeyboardEvent;
    }

    function seedTwoLive(): void {
      component.setInstances([
        mkInstance({ instance_id: 'a', status: 'running' }),
        mkInstance({ instance_id: 'b', status: 'running' }),
      ]);
    }

    it('ArrowDown advances focus to the next visible item', () => {
      seedTwoLive();
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[0]));
      expect(component.focusedItemId()).toBe('inst:live|inst:a');
      component.onTreeKeydown(makeKeyboardEvent('ArrowDown'));
      expect(component.focusedItemId()).toBe('inst:live|inst:b');
    });

    it('ArrowUp moves focus to the previous visible item', () => {
      seedTwoLive();
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[1]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowUp'));
      expect(component.focusedItemId()).toBe('inst:live|inst:a');
    });

    it('ArrowDown with no current focus (-1) lands on the first item', () => {
      seedTwoLive();
      component.onTreeKeydown(makeKeyboardEvent('ArrowDown'));
      expect(component.focusedItemId()).toBe('inst:live|inst:a');
    });

    it('ArrowDown/Up CLAMP at the ends (no wrap)', () => {
      seedTwoLive();
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[1]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowDown'));
      expect(component.focusedItemId()).toBe('inst:live|inst:b');
      component.onTreeKeydown(makeKeyboardEvent('ArrowUp'));
      component.onTreeKeydown(makeKeyboardEvent('ArrowUp'));
      expect(component.focusedItemId()).toBe('inst:live|inst:a');
    });

    it('ArrowRight expands a collapsed instance node', () => {
      seedTwoLive();
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowRight'));
      expect(component.isExpanded('a')).toBe(true);
    });

    it('ArrowRight is a no-op on an already-expanded node (no toggle-fight)', () => {
      seedTwoLive();
      component.toggleInstance('a');
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowRight'));
      expect(component.isExpanded('a')).toBe(true);
    });

    it('ArrowRight is a no-op on a job child row', () => {
      component.setInstances([mkInstance({ instance_id: 'a', status: 'running' })]);
      component.setActiveJobs([createMockJob({ job_id: 'j-1', mission_id: 'a', status: 'processing' })]);
      component.toggleInstance('a');
      // Focus the job child (index 1).
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[1]));
      expect(component.focusedItemId()).toBe('inst:live|inst:a|job:j-1');
      component.onTreeKeydown(makeKeyboardEvent('ArrowRight'));
      // Expansion state unchanged (still expanded, not toggled off).
      expect(component.isExpanded('a')).toBe(true);
    });

    it('ArrowLeft collapses an expanded instance node', () => {
      seedTwoLive();
      component.toggleInstance('a');
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[0]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowLeft'));
      expect(component.isExpanded('a')).toBe(false);
    });

    it('ArrowLeft on a job child collapses the owning instance and refocuses it', () => {
      component.setInstances([mkInstance({ instance_id: 'a', status: 'running' })]);
      component.setActiveJobs([createMockJob({ job_id: 'j-1', mission_id: 'a', status: 'processing' })]);
      component.toggleInstance('a');
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[1])); // job child
      component.onTreeKeydown(makeKeyboardEvent('ArrowLeft'));
      expect(component.isExpanded('a')).toBe(false);
      expect(component.focusedItemId()).toBe('inst:live|inst:a');
    });

    it('ArrowLeft on a CHILD INSTANCE row collapses the parent and refocuses it', () => {
      component.setInstances([
        mkInstance({ instance_id: 'root', status: 'running', children: ['kid'] }),
        mkInstance({ instance_id: 'kid', parent_id: 'root', agent_id: 'worker', status: 'running' }),
      ]);
      component.toggleInstance('root');
      // items: root(0), kid(1)
      component.onRowFocus(instanceTreeItemId(component.visibleItems()[1]));
      component.onTreeKeydown(makeKeyboardEvent('ArrowLeft'));
      expect(component.isExpanded('root')).toBe(false);
      expect(component.focusedItemId()).toBe('inst:live|inst:root');
    });

    it('Enter/Space/Esc are NOT handled by onTreeKeydown (rows own them)', () => {
      seedTwoLive();
      const before = component.focusedItemId();
      component.onTreeKeydown(makeKeyboardEvent('Enter'));
      component.onTreeKeydown(makeKeyboardEvent('Escape'));
      expect(component.focusedItemId()).toBe(before);
    });
  });

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
      (component.jobClick.emit as jest.Mock).mockClear();
      component.onFooterClick();
      expect(component.jobClick.emit).not.toHaveBeenCalled();
    });
  });

  // ── Source-text pins (REAL files, readFileSync + __dirname) ─────────
  //
  // Mirror tests prove the panel's logic; the pins prove the REAL
  // component actually wires the design: ROW CLICK = NAVIGATE on
  // instance nodes, CHEVRON-ONLY expand (stopPropagation), keyboard
  // machinery, and section titles. A revert of any of these would
  // pass every mirror test — the pins flip instead.

  describe('template binding seams (source-text pin)', () => {
    let templateHtml: string;
    let componentTs: string;

    beforeAll(() => {
      const path = require('path');
      const fs = require('fs');
      const specDir = __dirname;
      const htmlPath = path.join(specDir, 'job-queue-panel.component.html');
      templateHtml = fs.readFileSync(htmlPath, 'utf-8');
      const tsPath = path.join(specDir, 'job-queue-panel.component.ts');
      componentTs = fs.readFileSync(tsPath, 'utf-8');
    });

    it('ROW CLICK = NAVIGATE: instance rows bind (click)="onInstanceRowClick(item.node)"', () => {
      // The design's core interaction: clicking an instance row (root
      // OR child) navigates. Both the live and the recent section
      // must carry the binding.
      const hits = templateHtml.split('(click)="onInstanceRowClick(item.node)"').length - 1;
      expect(hits).toBeGreaterThanOrEqual(2); // live + recent sections
    });

    it('Enter on instance rows routes through the SAME navigate handler (keymap)', () => {
      const hits = templateHtml.split(
        '(keydown.enter)="$event.preventDefault(); onInstanceRowClick(item.node)"'
      ).length - 1;
      expect(hits).toBeGreaterThanOrEqual(2);
    });

    it('F2: EVERY row (keydown.enter) binding preventDefaults the event', () => {
      // Live-smoke fix F2 (2026-09-08): Enter's browser default action
      // synthesizes a click on the element focused at default-action
      // time. closeMenu() restores focus to the menu TRIGGER mid-
      // dispatch, so without preventDefault the synthetic click hits
      // the trigger and toggleMenu() RE-OPENS the menu right after the
      // close — the panel "stays open" after keyboard navigation
      // (reproduced live 2×; mechanism + fix validated in a real
      // browser with a mock BE). A revert of ANY row binding to the
      // scalar `(keydown.enter)="handler(x)"` form re-opens the bug:
      // pin all three binding shapes (live+recent instance rows, live
      // +recent job rows, queued+recent-flat rows).
      const shapes = [
        ['$event.preventDefault(); onInstanceRowClick(item.node)', 2],
        ['$event.preventDefault(); onRowClick(item.job)', 2],
        ['$event.preventDefault(); onRowClick(job)', 2],
      ] as const;
      for (const [binding, min] of shapes) {
        const hits = templateHtml.split(`(keydown.enter)="${binding}"`).length - 1;
        expect(hits).toBeGreaterThanOrEqual(min);
      }
      // And NO bare (non-preventDefault) row Enter binding survives.
      // The lookahead is bounded to the attribute value ([^"]*) so it
      // can't be satisfied by preventDefault text appearing LATER in
      // the template.
      const bare = templateHtml.match(/\(keydown\.enter\)="(?![^"]*preventDefault)[^"]*(?:onInstanceRowClick|onRowClick)[^"]*"/g) ?? [];
      expect(bare).toEqual([]);
    });

    it('W2 chevron is a REAL focusable button (native activation for keyboard)', () => {
      // The chevron must be a <button type="button"> — the native
      // button activation emits click for Enter/Space, no separate
      // (keydown.enter) handler is needed or wanted.
      const buttonHits = templateHtml.split('<button').length - 1;
      expect(buttonHits).toBeGreaterThanOrEqual(2); // live + recent sections
      expect(templateHtml).toContain('class="instance-chevron"');
      expect(templateHtml).toContain('type="button"');
      // The OLD shape (role="button" span + dead (keydown.enter)) is gone.
      expect(templateHtml).not.toMatch(/class="instance-chevron"[^>]*role="button"/);
      expect(templateHtml).not.toMatch(/\(keydown\.enter\)="onChevronClick/);
    });

    it('W2 chevron binds aria-expanded to the expansion state (both sections)', () => {
      const hits = templateHtml.split('[attr.aria-expanded]="isExpanded(item.node.instance.instance_id)"').length - 1;
      expect(hits).toBeGreaterThanOrEqual(2); // live + recent sections
    });

    it('CHEVRON-ONLY expand: the chevron is a separate click surface calling onChevronClick', () => {
      // The chevron must NOT navigate — it calls the toggle handler
      // with $event so it can stopPropagation.
      expect(templateHtml).toContain('(click)="onChevronClick($event, item.node.instance.instance_id)"');
    });

    it('onChevronClick stops propagation — chevron click does NOT navigate', () => {
      const fn = componentTs.match(/onChevronClick\(event: Event, instanceId: string\): void \{[\s\S]*?\n  \}/)?.[0] ?? '';
      expect(fn).toContain('event.stopPropagation()');
      expect(fn).toContain('this.toggleInstance(instanceId)');
    });

    it('instance rows carry (focus) mirroring — T3 focused-state wiring', () => {
      expect(templateHtml).toContain('(focus)="onRowFocus(');
    });

    it('binds [class.focused] on each tree row — T3 visible focus indicator', () => {
      expect(templateHtml).toContain('[class.focused]="isFocusedItem(');
    });

    it('binds (keydown) on the panel-list to onTreeKeydown — arrow nav', () => {
      expect(templateHtml).toContain('(keydown)="onTreeKeydown($event)"');
    });

    it('renders the three locked sections: Live conversations / Queued / Recent', () => {
      expect(templateHtml).toContain('Live conversations');
      expect(templateHtml).toContain('Queued');
      expect(templateHtml).toContain('Recent');
    });

    it('receipt rows keep the mission-liveness chip (Fix C unchanged)', () => {
      expect(templateHtml).toContain('<app-mission-liveness-chip');
      expect(templateHtml).toContain('[chip]="chip"');
    });
  });

  // W-zero-h-scroll — the panel-side half of the no-horizontal-scroll
  // contract: rows must TRUNCATE (ellipsis / clamp / wrap) so the
  // mat-menu shell stays scrollbar-free. Pins read the REAL SCSS.
  describe('panel SCSS truncation contract (source-text pin)', () => {
    let panelScss: string;

    beforeAll(() => {
      const path = require('path');
      const fs = require('fs');
      const scssPath = path.join(__dirname, 'job-queue-panel.component.scss');
      panelScss = fs.readFileSync(scssPath, 'utf-8');
    });

    it('panel-list forbids a horizontal scrollbar: overflow-x: hidden', () => {
      const block = panelScss.match(/\.panel-list\s*\{[^}]*\}/)?.[0] ?? '';
      expect(block).toContain('overflow-x: hidden');
    });

    it('instance-meta wraps pathological tokens instead of spilling: overflow-wrap: anywhere', () => {
      const block = panelScss.match(/\.instance-meta\s*\{[^}]*\}/)?.[0] ?? '';
      expect(block).toContain('overflow-wrap: anywhere');
    });

    it('job-name still truncates with ellipsis (title truncation contract)', () => {
      const block = panelScss.match(/\.job-name\s*\{[^}]*\}/)?.[0] ?? '';
      expect(block).toContain('white-space: nowrap');
      expect(block).toContain('text-overflow: ellipsis');
    });
  });

  // W4 — source-drift pins on the REAL component TS.
  describe('component TS source-drift pins', () => {
    let componentTs: string;

    beforeAll(() => {
      const path = require('path');
      const fs = require('fs');
      const specDir = __dirname;
      const tsPath = path.join(specDir, 'job-queue-panel.component.ts');
      componentTs = fs.readFileSync(tsPath, 'utf-8');
    });

    it('onTreeKeydown moves REAL DOM focus via document.getElementById — W1 mechanism', () => {
      expect(componentTs).toContain('document.getElementById');
    });

    it('onTreeKeydown filter keys include ArrowDown / ArrowUp / ArrowLeft / ArrowRight', () => {
      expect(componentTs).toContain("'ArrowDown'");
      expect(componentTs).toContain("'ArrowUp'");
      expect(componentTs).toContain("'ArrowLeft'");
      expect(componentTs).toContain("'ArrowRight'");
    });

    it('tree comes from the pure buildInstanceTree model helper (instances + jobs)', () => {
      expect(componentTs).toContain('buildInstanceTree(this.instances(), this.activeJobs(), this.recentJobs())');
    });

    it('auto-seed effect uses shouldAutoExpandInstanceTree (first-2-live lock)', () => {
      expect(componentTs).toContain('shouldAutoExpandInstanceTree');
    });

    it('G1: auto-seed tracks user-touched ids in a second Set (auto-seed filter)', () => {
      expect(componentTs).toContain('userTouchedInstances');
      // Auto-seed filters seeds by `!touched.has(id)` so user-decided
      // ids never enter the seed set. Multiline-tolerant regex.
      expect(componentTs).toMatch(/shouldAutoExpandInstanceTree\([\s\S]*?\)\s*\.filter\(/);
      expect(componentTs).toContain('!touched.has(id)');
      // Auto-seed MERGES onto the existing expansion set, never replaces.
      expect(componentTs).toMatch(/expandedInstances\.set\(next\)/);
      expect(componentTs).not.toMatch(/expandedInstances\.set\(new Set\(seedIds\)\)/);
    });

    it('G1: toggleInstance marks the id as user-touched', () => {
      expect(componentTs).toMatch(/toggleInstance[\s\S]*userTouchedInstances\.update/);
    });

    it('expansion state is ONE Set keyed by instance_id (survives polls)', () => {
      expect(componentTs).toContain('expandedInstances = signal<Set<string>>(new Set())');
    });
  });

  // ── helpers ─────────────────────────────────────────────────────────
  function createMockJobWithStatusLike(status: JobStatus): Job {
    return createMockJob({ status });
  }
});
